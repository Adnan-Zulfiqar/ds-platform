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
from app.core.exceptions import AppError, ConflictError
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.aliexpress.exceptions import (
    AliExpressError,
    AliExpressTimeoutError,
    AliExpressUnavailableError,
)
from app.integrations.aliexpress.ordering import PLACE_METHOD, parse_place, place_params
from app.integrations.aliexpress.service import AliExpressService
from app.models.order import PaymentStatus
from app.models.supplier_order import SupplierOrderStatus
from app.repositories.supplier_order import SupplierOrderRepository
from app.services.entitlements import BillingGate
from app.services.feature_flags import SUPPLIER_AUTO_ORDERING, FeatureFlagService
from app.services.supplier_ordering import SupplierOrderingService
from app.services.supplier_tracking import SupplierTrackingService
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _place(tenant_id: uuid.UUID, supplier_order_id: uuid.UUID) -> str:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            service = SupplierOrderingService(session)
            started = await service.begin_placing(supplier_order_id)
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
            # The client (and any token refresh) is settled before anything
            # is sent, inside the transaction that commits "placing". If it
            # cannot be built, nothing reached AliExpress: a plain failure.
            try:
                client = await AliExpressService(session).authenticated_client()
            except Exception as exc:
                await service.record_outcome(
                    supplier_order_id,
                    ok=False,
                    order_ids=[],
                    error_code=getattr(exc, "code", None) or type(exc).__name__,
                    error_message=str(exc) or "AliExpress is not connected.",
                )
                return "failed"

        try:
            # Exactly once: a retried create could buy the goods twice.
            payload = await client.call(PLACE_METHOD, params, retry=False)
            outcome = parse_place(payload)
            if outcome.unknown:
                await _record_unknown(supplier_order_id, reason=outcome.error_code or "unclear")
                logger.warning(
                    "supplier_order_outcome_unknown",
                    order_id=str(order_id),
                    error=outcome.error_code,
                )
                return "unknown"
        except (AliExpressTimeoutError, AliExpressUnavailableError) as exc:
            # No answer, or a server error: AliExpress may have created the
            # order anyway. Not "failed" (that invites a retry): it stays
            # "placing" until the merchant has checked AliExpress.
            await _record_unknown(supplier_order_id, reason=exc.code)
            logger.warning("supplier_order_outcome_unknown", order_id=str(order_id), error=exc.code)
            return "unknown"
        except AliExpressError as exc:
            # A refusal is an answer: AliExpress did not create the order.
            outcome = parse_place({})
            outcome = type(outcome)(
                False, [], getattr(exc, "upstream_code", None) or exc.code, str(exc)
            )
        except Exception as exc:  # anything else after sending: also unknown
            await _record_unknown(supplier_order_id, reason=type(exc).__name__)
            logger.exception("supplier_order_outcome_unknown", order_id=str(order_id))
            return "unknown"

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


async def _record_unknown(supplier_order_id: uuid.UUID, *, reason: str) -> None:
    async with transaction() as session:
        await SupplierOrderingService(session).record_unknown(supplier_order_id, reason=reason)


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="supplier_orders.place",
    max_retries=0,
    # BaseTask retries every exception; placing must never be repeated.
    autoretry_for=(),
)
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


# --- automatic mode ----------------------------------------------------------


async def _auto_place(tenant_id: uuid.UUID, order_ids: list[uuid.UUID]) -> int:
    """Queue placement for paid orders that have never been sent, when the
    workspace's auto-order switch is on. An order that already has a supplier
    row (placed, failed, in review) is left alone: automatic mode never
    retries what a person should look at."""
    set_tenant_id(tenant_id)
    queued = 0
    try:
        async with transaction() as session:
            service = SupplierOrderingService(session)
            settings = await service.settings()
            if settings is None or not settings.auto_order:
                return 0
            if not await FeatureFlagService(session).is_enabled(SUPPLIER_AUTO_ORDERING):
                logger.info("supplier_auto_place_feature_disabled")
                return 0
            try:
                # The manual button is billing-gated; auto mode must be too.
                await BillingGate(session).require_can_write()
            except AppError:
                logger.info("supplier_auto_place_billing_inactive")
                return 0
            for order_id in order_ids:
                if await service.supplier_orders.for_order(order_id) is not None:
                    continue
                order = await service.orders.get_by_id(order_id)
                if order is None or order.payment_status is not PaymentStatus.PAID:
                    continue
                if not service.auto_eligible(order, settings):
                    continue  # placed before auto mode was switched on
                try:
                    row = await service.request(order_id, user_id=None, trigger="auto")
                except ConflictError:
                    continue  # the button got there first
                if row.status == SupplierOrderStatus.QUEUED.value:
                    place_after_commit(session, row.id)
                    queued += 1
        return queued
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="supplier_orders.auto_place")
def auto_place(self: Any, tenant_id: str, order_ids: list[str], **_: Any) -> int:
    """Idempotent: a second run finds the supplier rows the first created."""
    return asyncio.run(_auto_place(uuid.UUID(tenant_id), [uuid.UUID(i) for i in order_ids]))


def auto_order_after_commit(session: Any, order_id: uuid.UUID) -> None:
    """Called by each channel's order import for a paid order. Collects the
    ids for the transaction and queues one task per workspace on commit; a
    rollback drops them. Cheap when the switch is off: the task returns at
    the first query."""
    tenant_id = str(require_tenant_id())
    sync = session.sync_session
    pending: dict[str, list[str]] | None = sync.info.get("auto_order_ids")
    if pending is None:
        pending = {}
        sync.info["auto_order_ids"] = pending

        def on_commit(_session: object) -> None:
            batch = sync.info.pop("auto_order_ids", {}) or {}
            for tenant, ids in batch.items():
                try:
                    auto_place.delay(tenant, list(dict.fromkeys(ids)))
                except Exception as exc:  # broker down: the order is saved
                    logger.warning("supplier_auto_place_enqueue_failed", error=type(exc).__name__)

        def on_rollback(_session: object) -> None:
            sync.info.pop("auto_order_ids", None)

        event.listen(sync, "after_commit", on_commit, once=True)
        event.listen(sync, "after_rollback", on_rollback, once=True)
    pending.setdefault(tenant_id, []).append(str(order_id))


# --- tracking -----------------------------------------------------------------


async def _sync_tracking(tenant_id: uuid.UUID, *, limit: int) -> dict[str, int]:
    """Check every placed order for a tracking number, one transaction per
    order so one failure never undoes another's progress."""
    set_tenant_id(tenant_id)
    counts = {"checked": 0, "found": 0, "pushed": 0, "failed": 0}
    try:
        async with transaction() as session:
            rows = SupplierOrderRepository(session)
            ids = [row.id for row in await rows.awaiting_tracking(limit=limit)]
            # A placement whose enqueue was lost (broker down) would sit in
            # "queued" forever. Only "queued" rows are ever placed, so sending
            # them again cannot place twice.
            for stale in await rows.stale_queued():
                place_after_commit(session, stale.id)
        for row_id in ids:
            try:
                async with transaction() as session:
                    service = SupplierTrackingService(session)
                    row = await service.supplier_orders.get_locked(row_id)
                    if row is None or row.status != SupplierOrderStatus.PLACED.value:
                        continue
                    counts["checked"] += 1
                    tracking = await service.check(row)
                    if tracking is None:
                        continue
                    counts["found"] += 1
                    if not await service.auto_push_enabled():
                        continue
                    if (row.error_code or "").startswith("tracking_push:"):
                        # The store refused before; repeating every three
                        # hours would fail the same way. The merchant pushes.
                        continue
                    try:
                        async with session.begin_nested():
                            await service.push(row)
                        counts["pushed"] += 1
                    except Exception as exc:  # a store refusal is recorded, not fatal
                        await service.record_push_failure(row, exc)
                        counts["failed"] += 1
            except AliExpressError as exc:
                logger.warning(
                    "supplier_tracking_check_failed",
                    supplier_order_id=str(row_id),
                    error=type(exc).__name__,
                )
        return counts
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="supplier_orders.sync_tracking_one")
def sync_tracking_one(self: Any, tenant_id: str, limit: int = 200, **_: Any) -> dict[str, int]:
    return asyncio.run(_sync_tracking(uuid.UUID(tenant_id), limit=limit))


@celery_app.task(base=BaseTask, bind=True, name="supplier_orders.sync_tracking_all")
def sync_tracking_all(self: Any, **_: Any) -> dict[str, int]:
    """Fan out to every workspace with a connected AliExpress account, the
    same set the order sync already sweeps (no new unscoped lookup)."""
    from app.tasks.orders import _connected_tenants

    tenants = asyncio.run(_connected_tenants())
    for tenant_id in tenants:
        sync_tracking_one.delay(str(tenant_id))
    return {"queued": len(tenants)}


__all__ = [
    "auto_order_after_commit",
    "auto_place",
    "place",
    "place_after_commit",
    "sync_tracking_all",
    "sync_tracking_one",
]
