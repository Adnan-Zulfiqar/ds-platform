"""Celery application.

Configured in Phase 0 so that the first real job would be a single task
function rather than an infrastructure project; tasks arrived in Phases 3-5
(integration health checks, catalogue sync, order sync). Getting the
reliability settings right early is much cheaper than discovering them under
production load.

**Broker choice.** RabbitMQ brokers the work and Redis stores results. Redis
alone would be simpler, but Redis is not a durable message broker: a restart can
lose queued work, which for an order-fulfilment job means a customer's order is
silently never placed. RabbitMQ persists queues and acknowledges delivery.
Results are transient status records, so Redis is the right home for those.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging, task_postrun, task_prerun

from app.core.config import settings
from app.core.context import RequestContext, clear_context
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

celery_app = Celery("droppilot")

celery_app.conf.update(
    broker_url=settings.celery.broker_url,
    result_backend=settings.celery.result_backend,
    # JSON only. Celery's pickle serialiser executes arbitrary code on
    # deserialisation, which turns broker access into remote code execution.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Store timestamps as UTC, consistent with the database.
    timezone="UTC",
    enable_utc=True,
    task_default_queue=settings.celery.task_default_queue,
    # Acknowledge after completion rather than on receipt, so a task whose
    # worker is killed mid-run is redelivered instead of lost. This makes
    # at-least-once delivery the contract: every task must be idempotent,
    # because it can legitimately run twice.
    task_acks_late=settings.celery.task_acks_late,
    task_reject_on_worker_lost=True,
    # Fetch one task at a time. The default of 4 lets a single worker hoard a
    # batch of long jobs while its siblings sit idle.
    worker_prefetch_multiplier=settings.celery.worker_prefetch_multiplier,
    # Recycle worker processes periodically to bound memory growth from leaks
    # in long-lived third-party clients.
    worker_max_tasks_per_child=settings.celery.worker_max_tasks_per_child,
    task_soft_time_limit=settings.celery.task_soft_time_limit,
    task_time_limit=settings.celery.task_time_limit,
    # Report STARTED so the UI can distinguish "queued" from "running". Costs
    # one extra backend write per task.
    task_track_started=True,
    result_expires=3600,
    # Fail fast if the broker is unreachable at startup rather than blocking
    # forever with no diagnostic.
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=10,
    # Task modules the worker must import to register them. A task that is
    # defined but never imported by the worker fails at call time with
    # "unregistered task", which is a confusing error to debug.
    #
    # Implementations live in app.tasks (see that package for why it sits
    # beside app.api rather than inside this one).
    imports=(
        "app.tasks.integrations.aliexpress",
        "app.tasks.products",
        "app.tasks.orders",
        "app.tasks.inventory",
        "app.tasks.pricing",
        "app.tasks.automation",
        "app.tasks.shipments",
        "app.tasks.analytics",
        "app.tasks.notifications",
    ),
    # Periodic schedule, executed by a beat process (`celery -A ... beat`).
    # Configuration only: no beat process runs on the development machine, so
    # these entries are registered but have never fired (M15). Intervals are
    # deliberately conservative — every scheduled sync spends tenants' supplier
    # quota, and the windows overlap the interval so a missed beat leaves
    # overlap rather than a gap.
    beat_schedule={
        "orders-sync-all": {
            "task": "orders.sync_all",
            "schedule": 60 * 60,  # hourly; the sync window is 2 days
        },
        "orders-refresh-status": {
            "task": "orders.refresh_status",
            "schedule": 60 * 60 * 6,
        },
        "orders-cleanup": {
            "task": "orders.cleanup",
            "schedule": 60 * 60 * 24,
        },
        "products-sweep-stale": {
            "task": "products.sweep_stale",
            "schedule": 60 * 60 * 12,  # matches the 12h staleness threshold
        },
        "inventory-sync": {
            "task": "inventory.sync",
            "schedule": 60 * 60 * 6,
        },
        "pricing-recalculate": {
            "task": "pricing.recalculate",
            "schedule": 60 * 60 * 12,
        },
        "automation-run": {
            "task": "automation.run",
            "schedule": 60 * 60,
        },
        "shipment-refresh": {
            "task": "shipment.refresh",
            "schedule": 60 * 60 * 6,
        },
        "analytics-aggregate": {
            "task": "analytics.aggregate",
            "schedule": 60 * 60,
        },
        "cleanup-old-notifications": {
            "task": "cleanup.old_notifications",
            "schedule": 60 * 60 * 24,
        },
        "aliexpress-sweep-health": {
            "task": "integrations.aliexpress.sweep_health_checks",
            "schedule": 60 * 60,
        },
    },
)

# Explicit routing table.
#
# Queue separation is very hard to retrofit: once every job shares one queue, a
# flood of slow imports blocks time-sensitive order fulfilment behind it.
#
# Integration work goes to its own queue because it is bounded by a third
# party's latency and quota rather than by our own capacity. A supplier having a
# slow morning must not delay anything else.
celery_app.conf.task_routes = {
    "integrations.*": {"queue": "integrations"},
}


@setup_logging.connect
def _configure_worker_logging(**_kwargs: Any) -> None:
    """Use the application's structured logging inside workers.

    Without this, Celery installs its own handlers and worker logs arrive in a
    different shape from API logs, breaking any query that spans both.
    """
    configure_logging()


@task_prerun.connect
def _bind_task_context(
    task_id: str | None = None,
    task: Any = None,
    kwargs: dict[str, Any] | None = None,
    **_extra: Any,
) -> None:
    """Restore request context from the task payload.

    Context variables do not cross a process boundary. A task that was enqueued
    during a request carries a ``_context`` entry in its kwargs; rebinding it
    here means worker logs join up with the request that scheduled them, and any
    tenant-scoped repository the task uses is correctly filtered.
    """
    context_payload = (kwargs or {}).get("_context")
    if isinstance(context_payload, dict):
        RequestContext.from_dict(context_payload).bind()

    logger.info(
        "task_started",
        task_id=task_id,
        task_name=getattr(task, "name", None),
    )


@task_postrun.connect
def _clear_task_context(
    task_id: str | None = None,
    task: Any = None,
    state: str | None = None,
    **_extra: Any,
) -> None:
    """Clear context when a task finishes.

    Mandatory, not hygiene. Worker processes reuse threads across tasks, so a
    leaked tenant id would silently scope the *next* task to the wrong customer.
    """
    logger.info(
        "task_finished",
        task_id=task_id,
        task_name=getattr(task, "name", None),
        state=state,
    )
    clear_context()


__all__ = ["celery_app"]
