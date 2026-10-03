"""WooCommerce background tasks (Track E7, W3)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from typing import Any

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.woocommerce.price_quantity import WooCommercePriceQuantitySync
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(base=BaseTask, bind=True, name="woocommerce.push_price_quantity")
def push_price_quantity(self: Any, tenant_id: str, product_id: str, **_: Any) -> dict[str, Any]:
    """Send one product's current price and stock to its WooCommerce products.

    Idempotent: the store receives absolute values. A store that is
    unreachable raises, so ``BaseTask`` retries with backoff. Refusals are
    recorded on the listing and the task completes.
    """
    set_tenant_id(uuid.UUID(tenant_id))
    try:

        async def _run() -> dict[str, Any]:
            async with transaction() as session:
                outcome = await WooCommercePriceQuantitySync(session).push(uuid.UUID(product_id))
                return {
                    "listings": outcome.listings,
                    "synced": outcome.synced,
                    "failed": outcome.failed,
                }

        return asyncio.run(_run())
    finally:
        clear_context()


def enqueue_price_quantity(tenant_id: uuid.UUID, product_ids: Iterable[uuid.UUID]) -> int:
    """Queue a push per product, after the change has committed. A broker
    outage is logged and swallowed, as for eBay: the edit itself is saved."""
    queued = 0
    for product_id in dict.fromkeys(product_ids):
        try:
            push_price_quantity.delay(str(tenant_id), str(product_id))
            queued += 1
        except Exception as exc:  # broker down: the edit is saved; see docstring
            logger.warning(
                "woocommerce_price_quantity_enqueue_failed",
                product_id=str(product_id),
                error=type(exc).__name__,
            )
    return queued


__all__ = ["enqueue_price_quantity", "push_price_quantity"]
