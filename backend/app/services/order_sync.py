"""Order synchronisation and fulfilment tracking.

The flow, mirroring the product importer it sits beside:

    request → AliExpress client → contract schema → domain → repository

**Reuses the Phase 3 client unchanged** — no signing, token handling or HTTP
appears here.

Every run writes an ``OrderSyncRun`` row whether it succeeds or not; the
failures are the rows worth reading. Every status change writes an
``OrderEvent``, which is what the timeline endpoint replays.

**Honesty note on the supplier contract.** The order *envelope* is pinned by a
live capture; the populated body and the status vocabulary below are derived
from AliExpress documentation because this account is not a registered DS
publisher and cannot retrieve a real order (recorded as M16 in
``TECHNICAL_DEBT.md``). Unknown statuses therefore degrade to "keep the current
state and record what the supplier said" rather than guessing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ValidationError
from app.core.redis import RedisPurpose, get_redis
from app.integrations.aliexpress.exceptions import AliExpressAuthError, AliExpressError
from app.integrations.aliexpress.orders import (
    OrderDetail,
    envelope_status,
    parse_commission_orders,
    parse_order_detail,
)
from app.integrations.aliexpress.service import AliExpressService
from app.integrations.aliexpress.webhook import WEBHOOK_COUNTER_KEY
from app.models.order import (
    FulfillmentStatus,
    Order,
    OrderEventType,
    OrderSource,
    OrderSyncRun,
    PaymentStatus,
    ShipmentStatus,
    SyncRunStatus,
    SyncTrigger,
    can_transition,
)
from app.repositories.order import (
    OrderEventRepository,
    OrderItemRepository,
    OrderRepository,
    OrderSyncRunRepository,
    ShipmentRepository,
    TrackingEventRepository,
)
from app.services.base import BaseService

#: AliExpress method names — named constants for the same reason as the
#: product importer: a typo returns `InvalidApiPath` with no hint of the
#: call site.
_ORDER_DETAIL_METHOD = "aliexpress.ds.trade.order.get"
_ORDER_LIST_METHOD = "aliexpress.ds.commissionorder.listbyindex"

#: Upper bound on list pages per run. A runaway pagination loop against a
#: quota-limited gateway is self-harm; a window that genuinely holds more
#: pages than this is a backfill, not a sync.
_MAX_PAGES = 20

#: Statuses the list endpoint is queried for. The parameter is mandatory
#: upstream, and these are the documented values that describe orders worth
#: importing. Documentation-derived (M16).
_LIST_STATUSES = ("Payment Completed", "Buyer Confirmed Receipt", "Completed Settlement")

#: Supplier order status → platform fulfilment vocabulary.
#: Documentation-derived (M16); the raw value is stored alongside on the order
#: so a wrong mapping is recoverable rather than destructive.
_FULFILLMENT_MAP: dict[str, FulfillmentStatus] = {
    "PLACE_ORDER_SUCCESS": FulfillmentStatus.AWAITING_PAYMENT,
    "WAIT_SELLER_EXAMINE_MONEY": FulfillmentStatus.AWAITING_PAYMENT,
    "FUND_PROCESSING": FulfillmentStatus.PAID,
    "WAIT_SELLER_SEND_GOODS": FulfillmentStatus.PROCESSING,
    "SELLER_PART_SEND_GOODS": FulfillmentStatus.FULFILLED,
    "WAIT_BUYER_ACCEPT_GOODS": FulfillmentStatus.SHIPPED,
    "FINISH": FulfillmentStatus.DELIVERED,
    "IN_CANCEL": FulfillmentStatus.CANCELLED,
    "IN_ISSUE": FulfillmentStatus.DISPUTED,
    "IN_FROZEN": FulfillmentStatus.DISPUTED,
    "RISK_CONTROL": FulfillmentStatus.PENDING,
}

#: Supplier statuses that imply payment cleared.
_PAID_STATUSES = frozenset(
    {
        "FUND_PROCESSING",
        "WAIT_SELLER_SEND_GOODS",
        "SELLER_PART_SEND_GOODS",
        "WAIT_BUYER_ACCEPT_GOODS",
        "FINISH",
    }
)
_UNPAID_STATUSES = frozenset({"PLACE_ORDER_SUCCESS", "WAIT_SELLER_EXAMINE_MONEY"})

#: Supplier logistics status → shipment vocabulary. Documentation-derived (M16).
_SHIPMENT_MAP: dict[str, ShipmentStatus] = {
    "WAIT_SELLER_SEND_GOODS": ShipmentStatus.PENDING,
    "SELLER_SEND_PART_GOODS": ShipmentStatus.IN_TRANSIT,
    "SELLER_SEND_GOODS": ShipmentStatus.IN_TRANSIT,
    "BUYER_ACCEPT_GOODS": ShipmentStatus.DELIVERED,
    "NO_LOGISTICS": ShipmentStatus.UNKNOWN,
}


def map_fulfillment_status(raw: str | None) -> FulfillmentStatus | None:
    """Translate a supplier status, or ``None`` when it has no mapping.

    ``None`` rather than a guess: an unmapped status keeps the order's current
    state and shows up in the timeline verbatim, which is exactly the case a
    human should look at.
    """
    if not raw:
        return None
    return _FULFILLMENT_MAP.get(raw.strip().upper())


def map_payment_status(raw: str | None) -> PaymentStatus:
    if not raw:
        return PaymentStatus.UNKNOWN
    normalised = raw.strip().upper()
    if normalised in _PAID_STATUSES:
        return PaymentStatus.PAID
    if normalised in _UNPAID_STATUSES:
        return PaymentStatus.UNPAID
    return PaymentStatus.UNKNOWN


def map_shipment_status(raw: str | None) -> ShipmentStatus:
    if not raw:
        return ShipmentStatus.UNKNOWN
    return _SHIPMENT_MAP.get(raw.strip().upper(), ShipmentStatus.UNKNOWN)


class OrderSyncService(BaseService):
    """Imports and refreshes orders from a connected supplier."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.orders = OrderRepository(session)
        self.items = OrderItemRepository(session)
        self.shipments = ShipmentRepository(session)
        self.tracking = TrackingEventRepository(session)
        self.events = OrderEventRepository(session)
        self.runs = OrderSyncRunRepository(session)
        self.integration = AliExpressService(session)
        #: A status change detected during an upsert, written to the timeline
        #: together with the event that triggered the sync touch.
        self._pending_status_event: tuple[str, str, str] | None = None

    # -- Synchronisation ------------------------------------------------------

    async def sync_orders(
        self,
        *,
        since_days: int = 7,
        trigger: SyncTrigger = SyncTrigger.MANUAL,
        requested_by_user_id: uuid.UUID | None = None,
    ) -> OrderSyncRun:
        """Run one incremental synchronisation for the current tenant.

        **Idempotent.** Orders are upserted by ``(tenant_id, source,
        external_id)``, enforced by the unique constraint rather than by a
        check a concurrent run could race past. Running the same window twice
        updates rather than duplicates.

        A run already in flight is rejected rather than doubled — that covers
        both the double-clicked button and the scheduled sweep landing on top
        of a manual run.
        """
        if await self.runs.find_running() is not None:
            raise ConflictError("An order synchronisation is already running.")

        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(days=since_days)

        run = await self.runs.create(
            trigger=trigger,
            status=SyncRunStatus.RUNNING,
            window_start=window_start,
            window_end=window_end,
            requested_by_user_id=requested_by_user_id,
            started_at=window_end,
        )
        # Make the RUNNING row visible before the long supplier conversation
        # starts, so a concurrent request sees it and is rejected above.
        await self.session.flush()

        seen = created = updated = 0
        detail_failures = 0

        try:
            external_ids = await self._list_order_ids(
                window_start=window_start, window_end=window_end
            )
        except AliExpressError as exc:
            await self._finish(run, SyncRunStatus.FAILED, code=type(exc).__name__, message=str(exc))
            raise

        for external_id in external_ids:
            seen += 1
            try:
                detail = await self._fetch_order(external_id)
            except AliExpressAuthError as exc:
                # An account-level refusal — "publisher not registered",
                # captured live — means *no* order is retrievable. Failing the
                # run loudly beats recording every order as individually
                # missing, which would look like catalogue churn.
                await self._finish(
                    run, SyncRunStatus.FAILED, code=type(exc).__name__, message=str(exc)
                )
                raise
            except AliExpressError as exc:
                detail_failures += 1
                self.logger.warning(
                    "order_detail_fetch_failed", external_id=external_id, error=exc.code
                )
                continue
            if detail is None:
                # A well-formed envelope with no result: the order is not
                # visible to this account. Information, not a fault.
                detail_failures += 1
                continue
            _, was_created = await self._upsert_from_detail(detail)
            if was_created:
                created += 1
            else:
                updated += 1

        run.orders_seen = seen
        run.orders_created = created
        run.orders_updated = updated

        outcome = SyncRunStatus.PARTIAL if detail_failures else SyncRunStatus.SUCCEEDED
        await self._finish(run, outcome)

        self.logger.info(
            "orders_synced",
            run_id=str(run.id),
            seen=seen,
            created=created,
            updated=updated,
            failures=detail_failures,
            trigger=trigger.value,
        )
        return run

    async def refresh_order(self, order: Order) -> Order:
        """Re-fetch one order from the supplier and apply what came back.

        Used by the status-refresh sweep. A missing upstream order marks the
        local one rather than deleting it: order history is a business record,
        and the supplier hiding an order is a fact worth surfacing, not erasing.
        """
        try:
            detail = await self._fetch_order(order.external_id)
        except AliExpressError as exc:
            order.last_sync_error = str(exc)[:1024]
            await self.session.flush()
            raise

        if detail is None:
            order.last_sync_error = "The supplier no longer returns this order."
            order.last_synced_at = datetime.now(UTC)
            await self.session.flush()
            return order

        refreshed, _ = await self._upsert_from_detail(detail)
        return refreshed

    # -- Reads ----------------------------------------------------------------

    async def get_timeline(self, order_id: uuid.UUID) -> list[dict[str, Any]]:
        """The merged chronological story of an order.

        Lifecycle events and carrier scans live in different tables; the reader
        wants one list. Sorted here rather than by the database because the
        merge spans tables with different columns.
        """
        order = await self.orders.get_by_id_or_raise(order_id)

        entries: list[dict[str, Any]] = [
            {
                "kind": "order",
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "description": event.description,
                "location": None,
                "status": None,
                "occurred_at": event.occurred_at,
            }
            for event in await self.events.list_for_order(order.id)
        ]

        for shipment in await self.shipments.list_for_order(order.id):
            entries.extend(
                {
                    "kind": "tracking",
                    "event_type": None,
                    "from_status": None,
                    "to_status": None,
                    "description": scan.description,
                    "location": scan.location,
                    "status": scan.status,
                    "occurred_at": scan.occurred_at,
                }
                for scan in shipment.tracking_events
            )

        entries.sort(key=lambda entry: entry["occurred_at"])
        return entries

    async def statistics(self) -> dict[str, Any]:
        """The live status dashboard numbers.

        Computed from this platform's own tables plus one Redis counter. What
        cannot be honestly reported is ``None`` rather than fabricated.
        """
        by_status = await self.orders.count_by_status()
        total = sum(by_status.values())

        def _count(*statuses: FulfillmentStatus) -> int:
            return sum(by_status.get(status.value, 0) for status in statuses)

        last_run = await self.runs.latest()
        failed_recently = await self.runs.count_failed_since(datetime.now(UTC) - timedelta(days=7))

        webhook_count: int | None = None
        try:
            raw = await get_redis(RedisPurpose.CACHE).get(WEBHOOK_COUNTER_KEY)
            webhook_count = int(raw) if raw is not None else 0
        except (RedisError, OSError, ValueError):
            # Degrade, never fail: a cache outage must not take the dashboard
            # down with it. Null tells the UI "unknown", which is the truth.
            webhook_count = None

        return {
            "total_orders": total,
            "by_status": by_status,
            "pending_fulfillment": _count(
                FulfillmentStatus.PENDING,
                FulfillmentStatus.AWAITING_PAYMENT,
                FulfillmentStatus.PAID,
            ),
            "processing": _count(FulfillmentStatus.PROCESSING, FulfillmentStatus.FULFILLED),
            "delivered": _count(FulfillmentStatus.DELIVERED),
            "failed_syncs_last_7_days": failed_recently,
            "last_sync": last_run,
            "webhook_events_received": webhook_count,
        }

    # -- Upsert ---------------------------------------------------------------

    async def _upsert_from_detail(self, detail: OrderDetail) -> tuple[Order, bool]:
        """Create or update one order from a supplier payload.

        Children follow the catalogue's precedent: items are replaced (the
        supplier's line set is authoritative), shipments are upserted by
        tracking number so their scan history survives a re-sync.
        """
        external_id = detail.external_id
        if not external_id:
            raise ValidationError("The supplier payload carries no order identifier.")

        now = datetime.now(UTC)
        raw_status = detail.order_status
        address = detail.receipt_address

        values: dict[str, Any] = {
            "external_status": raw_status,
            "payment_status": map_payment_status(raw_status),
            "currency": detail.currency,
            "total_amount": detail.total_amount,
            "external_created_at": detail.created_at,
            "last_synced_at": now,
            "last_sync_error": None,
        }
        if address is not None:
            values.update(
                recipient_name=address.contact_person,
                recipient_phone=address.mobile_no,
                address_line1=address.address,
                address_line2=address.address2,
                city=address.city,
                province=address.province,
                postal_code=address.zip,
                country_code=(address.country or "")[:2].upper() or None,
                buyer_name=address.contact_person,
                buyer_country=(address.country or "")[:2].upper() or None,
            )

        existing = await self.orders.get_by_external_id(
            source=OrderSource.ALIEXPRESS, external_id=external_id
        )

        if existing is None:
            # A newly imported order is created *at* the supplier's current
            # state rather than walked there from PENDING. The order lived its
            # earlier lifecycle upstream before this platform ever saw it, so
            # "pending -> shipped" on first sight is history, not a violation.
            initial = map_fulfillment_status(raw_status) or FulfillmentStatus.PENDING
            order = await self.orders.create(
                source=OrderSource.ALIEXPRESS,
                external_id=external_id,
                fulfillment_status=initial,
                **values,
            )
            if initial is FulfillmentStatus.DELIVERED:
                order.delivered_at = now
            await self.session.flush()
            await self._record_event(
                order,
                OrderEventType.CREATED,
                description=f"Imported from AliExpress in state {initial.value}.",
                occurred_at=detail.created_at or now,
            )
            created = True
        else:
            for field, value in values.items():
                setattr(existing, field, value)
            order = existing
            created = False
            # Transition validation applies to *updates* only — see above.
            self._apply_fulfillment_status(order, raw_status)

        await self.session.flush()

        # Items: replace. The supplier's line set is authoritative.
        await self.items.delete_for_order(order.id)
        for item in detail.items:
            await self.items.create(
                order_id=order.id,
                external_item_id=(
                    str(item.child_order_id) if item.child_order_id is not None else None
                ),
                external_product_id=item.external_product_id,
                product_id=None,
                title=item.product_name,
                sku_attributes=item.sku_attr,
                quantity=item.quantity,
                unit_price=item.unit_price,
                currency=detail.currency,
                external_status=item.order_status or item.logistics_status,
            )

        # Shipments: upsert by tracking number so scan history survives.
        for logistics in detail.logistics:
            if not logistics.logistics_no:
                continue
            shipment = await self.shipments.get_by_tracking_number(
                order_id=order.id, tracking_number=logistics.logistics_no
            )
            status = map_shipment_status(detail.logistics_status)
            previous_status = shipment.status if shipment is not None else None
            if shipment is None:
                shipment = await self.shipments.create(
                    order_id=order.id,
                    tracking_number=logistics.logistics_no,
                    carrier=logistics.logistics_service,
                    status=status,
                    last_checked_at=now,
                )
                await self._record_event(
                    order,
                    OrderEventType.SHIPMENT,
                    description=f"Shipment {logistics.logistics_no} recorded.",
                    occurred_at=now,
                )
            else:
                shipment.status = status
                shipment.carrier = logistics.logistics_service or shipment.carrier
                shipment.last_checked_at = now
            if status is ShipmentStatus.DELIVERED and shipment.delivered_at is None:
                shipment.delivered_at = now

            # Append a scan only when status moves. A no-op refresh must not
            # flood the timeline; the logistics payload rarely carries full
            # carrier history (M16), so the current status is what we record.
            if previous_status is None or previous_status != status:
                await self.tracking.create(
                    shipment_id=shipment.id,
                    occurred_at=now,
                    status=detail.logistics_status or status.value,
                    description=f"Status: {detail.logistics_status or status.value}",
                    location=None,
                )

        await self.session.flush()
        await self._record_event(
            order, OrderEventType.SYNCED, description="Synchronised with supplier.", occurred_at=now
        )
        return order, created

    def _apply_fulfillment_status(self, order: Order, raw_status: str | None) -> None:
        """Move the order to the supplier-reported state, validating the move.

        The supplier is authoritative — refusing its truth would only make the
        local copy wrong — but a move the lifecycle does not permit is logged
        and recorded in the timeline, because it means either the mapping table
        or the lifecycle model is wrong, and both are worth a human's look.
        """
        target = map_fulfillment_status(raw_status)
        if target is None or target == order.fulfillment_status:
            return

        current = order.fulfillment_status
        legal = can_transition(current, target)
        if not legal:
            self.logger.warning(
                "fulfillment_transition_outside_lifecycle",
                order_id=str(order.id),
                from_status=current.value,
                to_status=target.value,
                supplier_status=raw_status,
            )

        order.fulfillment_status = target
        if target is FulfillmentStatus.DELIVERED and order.delivered_at is None:
            order.delivered_at = datetime.now(UTC)
        if target in (FulfillmentStatus.PAID, FulfillmentStatus.PROCESSING) and (
            order.paid_at is None
        ):
            order.paid_at = datetime.now(UTC)

        description = f"Supplier reported {raw_status}."
        if not legal:
            description += " Transition falls outside the modelled lifecycle."
        self._pending_status_event = (current.value, target.value, description)

    async def _record_event(
        self,
        order: Order,
        event_type: OrderEventType,
        *,
        description: str | None,
        occurred_at: datetime,
        from_status: str | None = None,
        to_status: str | None = None,
    ) -> None:
        await self.events.create(
            order_id=order.id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            description=description,
            occurred_at=occurred_at,
        )
        # A status change queued by _apply_fulfillment_status is written
        # alongside whatever event triggered the sync touch.
        pending = self._pending_status_event
        if pending is not None:
            from_value, to_value, change_description = pending
            self._pending_status_event = None
            await self.events.create(
                order_id=order.id,
                event_type=OrderEventType.STATUS_CHANGE,
                from_status=from_value,
                to_status=to_value,
                description=change_description,
                occurred_at=occurred_at,
            )

    async def _finish(
        self,
        run: OrderSyncRun,
        status: SyncRunStatus,
        *,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        run.status = status
        run.error_code = code
        run.error_message = message[:2048] if message else None
        run.finished_at = datetime.now(UTC)
        await self.session.flush()

    # -- Supplier access ------------------------------------------------------

    async def _list_order_ids(self, *, window_start: datetime, window_end: datetime) -> list[str]:
        """Collect order identifiers in the window, across pages and statuses.

        The list endpoint requires a status parameter, so the documented
        actionable statuses are queried in turn. Pagination is bounded by
        ``_MAX_PAGES`` per status — beyond that lies a backfill job, not a sync.
        """
        collected: dict[str, None] = {}  # insertion-ordered set

        for status in _LIST_STATUSES:
            for page in range(1, _MAX_PAGES + 1):
                payload = await self._call(
                    _ORDER_LIST_METHOD,
                    {
                        "start_time": window_start.strftime("%Y-%m-%d %H:%M:%S"),
                        "end_time": window_end.strftime("%Y-%m-%d %H:%M:%S"),
                        "status": status,
                        "page_no": str(page),
                        "page_size": "50",
                    },
                )
                orders, has_more = parse_commission_orders(payload)
                for row in orders:
                    if row.external_id:
                        collected[row.external_id] = None
                if not has_more:
                    break

        return list(collected)

    async def _fetch_order(self, external_id: str) -> OrderDetail | None:
        payload = await self._call(_ORDER_DETAIL_METHOD, {"order_id": external_id})
        detail = parse_order_detail(payload)
        if detail is None:
            code, message = envelope_status(payload)
            # 401 "This publisher is not registered" — captured live — is an
            # account-level authorization refusal, typed accordingly so the
            # sync loop can tell it apart from per-order churn.
            if code == 401:
                raise AliExpressAuthError(
                    message or "The supplier refused the order query for this account.",
                )
            return None
        return detail

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue a supplier call using the tenant's stored credentials.

        Identical to the product importer's helper, and kept separate
        deliberately: the two services page differently and fail differently,
        and coupling them so they could share nine lines would be a dependency
        neither needs.
        """
        client = await self.integration.authenticated_client()
        return await client.call(method, params)


__all__ = [
    "OrderSyncService",
    "map_fulfillment_status",
    "map_payment_status",
    "map_shipment_status",
]
