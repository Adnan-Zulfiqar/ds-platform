"""Placing supplier orders on AliExpress (Track F, D-017).

Three steps, each its own transaction, because the AliExpress call must sit
between two commits:

1. ``queued`` → ``placing`` (committed before the call);
2. the AliExpress call;
3. ``placed`` or ``failed`` with what AliExpress said.

A crash between 1 and 3 leaves ``placing``. That row is never retried here:
the merchant checks AliExpress, because a blind retry could buy the goods
twice. ``task_acks_late`` redelivery is therefore safe: a redelivered task
finds the row no longer ``queued`` and does nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import event

from app.core.context import clear_context, require_tenant_id, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.aliexpress.exceptions import AliExpressError
from app.integrations.aliexpress.ordering import PLACE_METHOD, parse_place, place_params
from app.integrations.aliexpress.service import AliExpressService
from app.services.supplier_ordering import SupplierOrderingService
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _place(tenant_id: uuid.UUID, supplier_order_id: uuid.UUID) -> str:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            started = await SupplierOrderingService(session).begin_placing(supplier_order_id)
            if started is None:
                return "skipped"
            row, review, fallback = started
            order_id = row.order_id
            assert review.address is not None  # begin_placing returns None otherwise
            params = place_params(
                out_order_id=str(order_id),
                address=review.address,
                lines=review.lines,
                shipping_method=fallback,
            )

        try:
            async with transaction() as session:
                client = await AliExpressService(session).authenticated_client()
                payload = await client.call(PLACE_METHOD, params)
            outcome = parse_place(payload)
        except AliExpressError as exc:
            # A refusal is an answer: AliExpress did not create the order.
            outcome = parse_place({})
            outcome = type(outcome)(
                False, [], getattr(exc, "upstream_code", None) or exc.code, str(exc)
            )

        async with transaction() as session:
            await SupplierOrderingService(session).record_outcome(
                supplier_order_id,
                ok=outcome.ok,
                order_ids=outcome.order_ids,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
        logger.info(
            "supplier_order_place_finished",
            order_id=str(order_id),
            ok=outcome.ok,
            orders=len(outcome.order_ids),
            error_code=outcome.error_code,
        )
        return "placed" if outcome.ok else "failed"
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="supplier_orders.place", max_retries=0)
def place(self: Any, tenant_id: str, supplier_order_id: str, **_: Any) -> str:
    """No automatic retries: a retry after an unknown outcome could double
    the order. A failure is recorded and the merchant retries."""
    return asyncio.run(_place(uuid.UUID(tenant_id), uuid.UUID(supplier_order_id)))


def place_after_commit(session: Any, supplier_order_id: uuid.UUID) -> None:
    """Queue the placement once the request's transaction has committed, so
    the task never reads a row that does not exist yet."""
    tenant_id = str(require_tenant_id())

    def on_commit(_session: object) -> None:
        try:
            place.delay(tenant_id, str(supplier_order_id))
        except Exception as exc:  # broker down: the row stays queued, visibly
            logger.warning("supplier_order_enqueue_failed", error=type(exc).__name__)

    event.listen(session.sync_session, "after_commit", on_commit, once=True)


__all__ = ["place", "place_after_commit"]
