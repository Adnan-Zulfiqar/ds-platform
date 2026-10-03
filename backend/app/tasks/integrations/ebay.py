"""Celery tasks for the eBay channel (EBAY-C4 onwards)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import event

from app.core.context import clear_context, require_tenant_id, set_tenant_id
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
        except Exception as exc:  # broker down: the edit is saved; see docstring
            logger.warning(
                "ebay_price_quantity_enqueue_failed",
                product_id=str(product_id),
                error=type(exc).__name__,
            )
    return queued


def push_price_quantity_after_commit(session: Any, product_ids: Iterable[uuid.UUID]) -> None:
    """Queue the pushes once the caller's transaction has committed — never
    before (the task would read the old values) and never on rollback.

    ``session`` is the request's ``AsyncSession``; the listener goes on its
    sync session, where SQLAlchemy emits the event. The tenant is read now,
    while the request context still holds it.
    """
    ids = list(dict.fromkeys(product_ids))
    if not ids:
        return
    tenant_id = require_tenant_id()

    def on_commit(_session: object) -> None:
        enqueue_price_quantity(tenant_id, ids)

    event.listen(session.sync_session, "after_commit", on_commit, once=True)


__all__ = ["enqueue_price_quantity", "push_price_quantity", "push_price_quantity_after_commit"]
