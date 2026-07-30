"""Base task class and enqueue helpers.

Two problems every background job in this platform shares, solved once here:

* **Context propagation.** ``contextvars`` do not survive a process hop, so a
  task enqueued during a request would run with no tenant and no correlation id.
  :func:`enqueue` attaches a serialised context that the worker rebinds.
* **Transient failure.** Marketplace APIs rate-limit and time out constantly.
  :class:`BaseTask` retries with exponential backoff and jitter by default, so
  individual tasks do not each reimplement it.
"""

from __future__ import annotations

from typing import Any

from celery import Task

from app.core.context import RequestContext
from app.core.logging import get_logger

logger = get_logger(__name__)

CONTEXT_KWARG = "_context"


class BaseTask(Task):
    """Default behaviour for application tasks.

    Tasks are retried automatically on any exception. Combined with
    ``task_acks_late``, delivery is at-least-once, so **every task must be
    idempotent** — running it twice with the same arguments must be
    indistinguishable from running it once. For an order-fulfilment job that
    means checking whether the order was already placed before placing it.
    """

    autoretry_for = (Exception,)
    max_retries = 3

    # Exponential backoff capped at ten minutes. Jitter is essential: without
    # it, a downstream outage causes every failed task to retry in lockstep and
    # hammer the recovering service in synchronised waves.
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """Log a task that has exhausted its retries.

        This is the signal that matters operationally — a task failing once is
        noise, a task failing permanently means customer work was dropped and
        should raise an alert.
        """
        logger.error(
            "task_failed_permanently",
            task_id=task_id,
            task_name=self.name,
            error=str(exc),
            error_type=type(exc).__name__,
            retries=self.request.retries,
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)

    def on_retry(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        logger.warning(
            "task_retrying",
            task_id=task_id,
            task_name=self.name,
            error=str(exc),
            retry_number=self.request.retries + 1,
        )
        super().on_retry(exc, task_id, args, kwargs, einfo)


def enqueue(task: Task, *args: Any, **kwargs: Any) -> Any:
    """Schedule a task with the current context attached.

    Always prefer this over ``task.delay(...)``. A task enqueued directly loses
    tenant context, and a tenant-scoped repository inside it will then raise
    rather than silently reading the wrong data — safe, but a needless failure.
    """
    kwargs[CONTEXT_KWARG] = RequestContext.current().to_dict()
    return task.apply_async(args=args, kwargs=kwargs)


__all__ = ["CONTEXT_KWARG", "BaseTask", "enqueue"]
