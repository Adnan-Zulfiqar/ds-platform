"""Shipment tracking refresh — reuses order status refresh."""

from __future__ import annotations

from typing import Any

from app.tasks.orders import refresh_status
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app


@celery_app.task(base=BaseTask, bind=True, name="shipment.refresh")
def refresh_shipments(self: Any, limit: int = 500, **_: Any) -> Any:
    """Refresh in-flight order shipments.

    TrackingEvent rows are written inside OrderSyncService when status moves.
    This task is an explicit alias so beat and automation can schedule
    ``shipment.refresh`` without coupling callers to the orders module name.
    """
    # ``run``, not a direct call with ``self``: ``refresh_status`` is a bound
    # task, so Celery supplies its own ``self`` and passing ours raised
    # ``TypeError: multiple values for 'limit'`` on every scheduled run.
    return refresh_status.run(limit=limit)
