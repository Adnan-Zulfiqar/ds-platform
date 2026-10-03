"""Celery tasks for the eBay channel (EBAY-C4 onwards)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from typing import Any

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.ebay.price_quantity import EbayPriceQuantitySync
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(base=BaseTask, bind=True, name="ebay.push_price_quantity")
def push_price_quantity(self: Any, tenant_id: str, product_id: str, **_: Any) -> dict[str, Any]:
    """Send one product's current price and quantity to its eBay listings.

    Idempotent: eBay receives absolute values. eBay unreachable raises, so
    ``BaseTask`` retries with backoff; refusals are recorded on the listing
    and the task completes.
    """
    set_tenant_id(uuid.UUID(tenant_id))
    try:

        async def _run() -> dict[str, Any]:
            async with transaction() as session:
                outcome = await EbayPriceQuantitySync(session).push(uuid.UUID(product_id))
                return {
                    "listings": outcome.listings,
                    "synced": outcome.synced,
                    "failed": outcome.failed,
                }

        return asyncio.run(_run())
    finally:
        clear_context()


def enqueue_price_quantity(tenant_id: uuid.UUID, product_ids: Iterable[uuid.UUID]) -> int:
    """Queue a push per product. Call only after the change has committed —
    a task that ran first would read the old values and send those.

    A broker outage is logged and swallowed: the change itself is saved, and
    the merchant can send it from the editor; failing the merchant's edit
    because RabbitMQ is down would be the worse outcome.
    """
    queued = 0
    for product_id in dict.fromkeys(product_ids):
        try:
            push_price_quantity.delay(str(tenant_id), str(product_id))
            queued += 1
        except Exception as exc:
            logger.warning(
                "ebay_price_quantity_enqueue_failed",
                product_id=str(product_id),
                error=type(exc).__name__,
            )
    return queued


__all__ = ["enqueue_price_quantity", "push_price_quantity"]
