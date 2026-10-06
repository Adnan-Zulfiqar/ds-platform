"""Order synchronisation tasks.

**Follows the established task pattern exactly** — see ``app.tasks.products``.
Each unit of work binds tenant context, opens its own transaction and event
loop, and clears context in ``finally``, because worker processes reuse threads
and a leaked tenant id would scope the next task to the wrong customer.

Every task is idempotent, which ``task_acks_late`` requires: the underlying
sync upserts orders on ``(tenant_id, source, external_id)``, so running twice
is indistinguishable from running once. A run already in flight for a tenant is
skipped rather than doubled — that is what makes redelivery safe rather than
merely tolerable.

> **Never executed under a broker.** No RabbitMQ is available on the
> development machine, so these tasks are registered and unit-tested but have
> never run under Celery (M15 in ``TECHNICAL_DEBT.md``). The beat schedule in
> ``workers/celery_app.py`` is likewise configuration that has never driven a
> live beat process.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.aliexpress.exceptions import AliExpressError
from app.models.integration import AliExpressConnection, IntegrationStatus
from app.models.order import OrderSource, OrderSyncRun, SyncTrigger
from app.repositories.order import OrderSyncRunRepository
from app.services.order_sync import OrderSyncService
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

#: Window the scheduled sync asks the supplier for. Wider than the schedule
#: interval so a missed beat leaves overlap, never a gap — overlap is free
#: because the sync is idempotent.
_SCHEDULED_WINDOW_DAYS = 2

#: Ceiling on in-flight orders refreshed per tenant per status sweep.
_REFRESH_LIMIT = 100

#: Finished sync runs older than this are purged by the cleanup task.
_RUN_RETENTION_DAYS = 90


async def _connected_tenants() -> list[uuid.UUID]:
    """Every tenant with a usable supplier connection.

    **Deliberately unscoped**, like the sweeps it mirrors: it runs on no
    tenant's behalf. Only tenant ids leave the transaction; each is re-entered
    under its own bound context by the per-tenant task.
    """
    async with transaction() as session:
        result = await session.execute(
            sa.select(AliExpressConnection.tenant_id).where(
                AliExpressConnection.status == IntegrationStatus.CONNECTED
            )
        )
        return [row[0] for row in result.all()]


async def _sync_tenant(tenant_id: uuid.UUID, *, since_days: int) -> bool:
    """Run one tenant's sync inside its own transaction and context."""
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            await OrderSyncService(session).sync_orders(
                since_days=since_days, trigger=SyncTrigger.SCHEDULED
            )
            return True
    except ConflictError:
        # A manual run is already in flight. Skipping is the correct outcome,
        # not a failure — the running sync is doing this task's work.
        logger.info("order_sync_skipped_already_running", tenant_id=str(tenant_id))
        return False
    finally:
        clear_context()


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="orders.sync_all",
    soft_time_limit=300,
    time_limit=360,
)
def sync_all(self: Any, since_days: int = _SCHEDULED_WINDOW_DAYS, **_: Any) -> int:
    """Fan out an order sync to every connected tenant.

    Fans out rather than syncing inline: one tenant's slow supplier
    conversation must not hold every other tenant's orders hostage, and a
    per-tenant task gets its own retry budget.
    """
    tenants = asyncio.run(_connected_tenants())

    for tenant_id in tenants:
        sync_one_store.delay(tenant_id=str(tenant_id), since_days=since_days)

    logger.info("order_sync_fanout", tenants=len(tenants))
    return len(tenants)


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="orders.sync_one_store",
    soft_time_limit=240,
    time_limit=300,
)
def sync_one_store(
    self: Any, tenant_id: str, since_days: int = _SCHEDULED_WINDOW_DAYS, **_: Any
) -> bool:
    """Synchronise one tenant's store connection.

    "Store" is the tenant's single AliExpress connection — the schema enforces
    one per tenant. When multi-store arrives, this task gains a store
    identifier and nothing above it changes.

    Permanent supplier failures are not retried: retrying a rejected token
    three times with backoff is three wasted calls that end the same way. The
    failure is already recorded on the ``OrderSyncRun`` row.
    """
    try:
        return asyncio.run(_sync_tenant(uuid.UUID(tenant_id), since_days=since_days))
    except AliExpressError as exc:
        if not exc.retryable:
            logger.warning(
                "order_sync_permanent_failure",
                tenant_id=tenant_id,
                error=type(exc).__name__,
            )
            return False
        raise


async def _refresh_tenant_orders(tenant_id: uuid.UUID, *, limit: int) -> int:
    """Re-fetch every in-flight order for one tenant."""
    set_tenant_id(tenant_id)
    refreshed = 0
    try:
        async with transaction() as session:
            service = OrderSyncService(session)
            orders = await service.orders.list_active_between(
                source=OrderSource.ALIEXPRESS, limit=limit
            )
            for order in orders:
                try:
                    await service.refresh_order(order)
                    refreshed += 1
                except AliExpressError:
                    # Recorded on the order's last_sync_error by refresh_order.
                    # One unfetchable order must not abort the rest.
                    continue
        return refreshed
    finally:
        clear_context()


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="orders.refresh_status",
    soft_time_limit=300,
    time_limit=360,
)
def refresh_status(self: Any, limit: int = _REFRESH_LIMIT, **_: Any) -> int:
    """Refresh fulfilment status for orders still in flight, all tenants.

    Terminal orders — delivered, cancelled, refunded — are excluded by the
    repository query: they no longer change upstream, so refreshing them
    spends quota to learn nothing.
    """
    tenants = asyncio.run(_connected_tenants())
    total = 0
    for tenant_id in tenants:
        total += asyncio.run(_refresh_tenant_orders(tenant_id, limit=limit))

    logger.info("order_status_refresh_complete", tenants=len(tenants), refreshed=total)
    return total


async def _cleanup_tenant(tenant_id: uuid.UUID, *, cutoff: datetime) -> int:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            return await OrderSyncRunRepository(session).delete_finished_before(cutoff)
    finally:
        clear_context()


async def _all_tenant_ids_with_runs() -> list[uuid.UUID]:
    """Tenants that have any sync history — the only ones cleanup can touch."""
    async with transaction() as session:
        result = await session.execute(sa.select(OrderSyncRun.tenant_id).distinct())
        return [row[0] for row in result.all()]


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="orders.cleanup",
    soft_time_limit=120,
    time_limit=180,
)
def cleanup(self: Any, retention_days: int = _RUN_RETENTION_DAYS, **_: Any) -> int:
    """Purge old finished sync runs across every tenant.

    Sync runs are operational telemetry, not business records — orders and
    their timelines are never touched here. The most recent runs are kept for
    every tenant regardless of age, so a rarely-syncing tenant never loses
    their entire history.
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    tenants = asyncio.run(_all_tenant_ids_with_runs())

    removed = 0
    for tenant_id in tenants:
        removed += asyncio.run(_cleanup_tenant(tenant_id, cutoff=cutoff))

    logger.info("order_sync_runs_purged", tenants=len(tenants), removed=removed)
    return removed


__all__ = ["cleanup", "refresh_status", "sync_all", "sync_one_store"]
