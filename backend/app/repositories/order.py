"""Order management data access.

Every class here extends :class:`TenantScopedRepository`, so the tenant
predicate is inherited rather than written per query — the same posture as the
catalogue, because a cross-tenant order leak exposes buyer names and addresses,
which is strictly worse than leaking a product title.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import (
    FulfillmentStatus,
    Order,
    OrderEvent,
    OrderItem,
    OrderSource,
    OrderSyncRun,
    Shipment,
    SyncRunStatus,
    TrackingEvent,
)
from app.repositories.base import TenantScopedRepository
from app.schemas.common import ListQueryParams


class OrderRepository(TenantScopedRepository[Order]):
    """Reads and writes for orders."""

    #: An allowlist, not a denylist — ``sort_by`` arrives from the query string.
    sortable_fields = frozenset(
        {
            "created_at",
            "updated_at",
            "external_created_at",
            "fulfillment_status",
            "total_amount",
            "last_synced_at",
        }
    )

    searchable_fields = frozenset({"external_id", "buyer_name", "recipient_name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Order)

    async def get_by_external_id(self, *, source: OrderSource, external_id: str) -> Order | None:
        """Find an order by its supplier identifier.

        The lookup that makes sync idempotent. Tenant-filtered through
        ``_base_query``, so one tenant's sync can never find — or overwrite —
        another tenant's row for the same supplier order.
        """
        query = self._base_query().where(
            Order.source == source,
            Order.external_id == external_id,
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def redact_buyer_fields(
        self, *, store_id: uuid.UUID, external_ids: Sequence[str] | None
    ) -> int:
        """Blank the buyer's personal details on this tenant's orders for one
        store: every order when ``external_ids`` is ``None`` (``shop/redact``),
        otherwise exactly the listed marketplace order ids
        (``customers/redact``). The country stays: it is not personal and the
        merchant's reporting uses it. Tenant predicate included, as in every
        write here.
        """
        if external_ids is not None and not external_ids:
            return 0
        statement = (
            update(Order)
            .where(
                Order.tenant_id == await self._current_tenant_id(),
                Order.store_id == store_id,
                Order.deleted_at.is_(None),
            )
            .values(
                buyer_name=None,
                marketplace_buyer_username=None,
                recipient_name=None,
                recipient_phone=None,
                address_line1=None,
                address_line2=None,
                city=None,
                province=None,
                postal_code=None,
            )
        )
        if external_ids is not None:
            statement = statement.where(Order.external_id.in_(list(external_ids)))
        result = await self.session.execute(statement)
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_orders(
        self,
        params: ListQueryParams,
        *,
        fulfillment_status: FulfillmentStatus | None = None,
        source: OrderSource | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[Order], int]:
        """One page of orders with the filters the list endpoint exposes.

        The date range applies to ``external_created_at`` — when the buyer
        placed the order — because that is the date a merchant thinks in, not
        when this platform happened to sync the row. Range predicates cannot be
        expressed through the generic exact-match ``filters`` argument, hence
        this method rather than a call-site workaround.
        """
        filters: dict[str, object] = {}
        if fulfillment_status is not None:
            filters["fulfillment_status"] = fulfillment_status
        if source is not None:
            filters["source"] = source

        query = self._apply_filters(self._base_query(), filters)
        if date_from is not None:
            query = query.where(Order.external_created_at >= date_from)
        if date_to is not None:
            query = query.where(Order.external_created_at <= date_to)
        query = self._apply_search(query, params)

        count_query = select(func.count()).select_from(query.subquery())
        total = int((await self.session.execute(count_query)).scalar_one())

        query = self._apply_sorting(query, params)
        query = query.offset(params.offset).limit(params.limit)

        rows = (await self.session.execute(query)).scalars().all()
        return list(rows), total

    async def count_by_status(self) -> dict[str, int]:
        """Order totals per fulfilment status, for the statistics endpoint."""
        where_clause = self._base_query().whereclause
        query = select(Order.fulfillment_status, func.count(Order.id)).select_from(Order)
        if where_clause is not None:
            query = query.where(where_clause)
        result = await self.session.execute(query.group_by(Order.fulfillment_status))
        return {str(status.value): count for status, count in result.all()}

    async def list_active_between(
        self, *, since: datetime | None = None, limit: int = 500
    ) -> list[Order]:
        """Orders still in flight, oldest sync first.

        Feeds the status-refresh task: terminal orders (delivered, cancelled,
        refunded) no longer change upstream, so refreshing them spends quota to
        learn nothing.
        """
        active = (
            FulfillmentStatus.PENDING,
            FulfillmentStatus.AWAITING_PAYMENT,
            FulfillmentStatus.PAID,
            FulfillmentStatus.PROCESSING,
            FulfillmentStatus.FULFILLED,
            FulfillmentStatus.SHIPPED,
            FulfillmentStatus.DISPUTED,
        )
        query = self._base_query().where(Order.fulfillment_status.in_(active))
        if since is not None:
            query = query.where(Order.external_created_at >= since)
        query = query.order_by(Order.last_synced_at.asc().nulls_first()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())


class OrderItemRepository(TenantScopedRepository[OrderItem]):
    """Order lines. Written by the sync, read with their parent."""

    sortable_fields = frozenset({"created_at"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrderItem)

    async def delete_for_order(self, order_id: uuid.UUID) -> None:
        """Replace rather than merge on re-sync.

        Same reasoning as product variants: the supplier's line set is
        authoritative, and diffing means guessing which remote line corresponds
        to which stored row.
        """
        query = self._base_query().where(OrderItem.order_id == order_id)
        result = await self.session.execute(query)
        for item in result.scalars().all():
            await self.session.delete(item)
        await self.session.flush()


class ShipmentRepository(TenantScopedRepository[Shipment]):
    """Shipments against orders."""

    sortable_fields = frozenset({"created_at", "updated_at", "shipped_at", "status"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Shipment)

    async def list_for_order(self, order_id: uuid.UUID) -> list[Shipment]:
        query = self._base_query().where(Shipment.order_id == order_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_tracking_number(
        self, *, order_id: uuid.UUID, tracking_number: str
    ) -> Shipment | None:
        """The upsert key for shipments during a re-sync.

        Scoped to the order as well as the tenant: carriers reuse tracking
        numbers across years, and two orders legitimately carrying the same
        number must not collapse into one shipment.
        """
        query = self._base_query().where(
            Shipment.order_id == order_id,
            Shipment.tracking_number == tracking_number,
        )
        result = await self.session.execute(query)
        return result.scalars().first()


class TrackingEventRepository(TenantScopedRepository[TrackingEvent]):
    """Carrier scans, append-only."""

    sortable_fields = frozenset({"created_at", "occurred_at"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, TrackingEvent)

    async def exists_for_shipment(
        self, *, shipment_id: uuid.UUID, occurred_at: datetime, status: str | None
    ) -> bool:
        """Whether this scan is already recorded.

        Tracking feeds re-deliver history on every poll; this is what keeps the
        timeline append-only without duplicating it.
        """
        query = self._base_query().where(
            TrackingEvent.shipment_id == shipment_id,
            TrackingEvent.occurred_at == occurred_at,
            TrackingEvent.status == status,
        )
        result = await self.session.execute(query.limit(1))
        return result.scalars().first() is not None


class OrderEventRepository(TenantScopedRepository[OrderEvent]):
    """Timeline entries, append-only. Never deleted — this is the audit trail."""

    sortable_fields = frozenset({"created_at", "occurred_at"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrderEvent)

    async def list_for_order(self, order_id: uuid.UUID) -> list[OrderEvent]:
        query = (
            self._base_query()
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.occurred_at.asc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())


class OrderSyncRunRepository(TenantScopedRepository[OrderSyncRun]):
    """Sync attempts — the audit trail. Mirrors ``ProductImportRepository``."""

    sortable_fields = frozenset({"created_at", "started_at", "finished_at", "status"})
    searchable_fields = frozenset({"error_code"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, OrderSyncRun)

    async def find_running(self) -> OrderSyncRun | None:
        """Whether a sync is already in flight for this tenant.

        Guards against a double-clicked sync button and against the scheduled
        sweep starting on top of a manual run.
        """
        query = self._base_query().where(OrderSyncRun.status == SyncRunStatus.RUNNING)
        result = await self.session.execute(query.limit(1))
        return result.scalars().first()

    async def latest(self) -> OrderSyncRun | None:
        query = self._base_query().order_by(OrderSyncRun.created_at.desc()).limit(1)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def count_failed_since(self, since: datetime) -> int:
        query = (
            select(func.count(OrderSyncRun.id))
            .select_from(OrderSyncRun)
            .where(
                OrderSyncRun.status == SyncRunStatus.FAILED,
                OrderSyncRun.created_at >= since,
            )
        )
        where_clause = self._base_query().whereclause
        if where_clause is not None:
            query = query.where(where_clause)
        result = await self.session.execute(query)
        return int(result.scalar_one())

    async def delete_finished_before(self, cutoff: datetime, *, keep_last: int = 20) -> int:
        """Purge old finished runs, keeping the most recent few regardless of age.

        The cleanup task's workhorse. A hard delete, deliberately: sync runs
        are operational telemetry, not business records, and a table that only
        grows eventually makes the statistics queries that read it slow.
        The most recent runs are kept even when old so a rarely-syncing tenant
        never loses their entire history.
        """
        keep_query = self._base_query().order_by(OrderSyncRun.created_at.desc()).limit(keep_last)
        keep_result = await self.session.execute(keep_query)
        keep_ids = {run.id for run in keep_result.scalars().all()}

        query = self._base_query().where(
            OrderSyncRun.status != SyncRunStatus.RUNNING,
            OrderSyncRun.created_at < cutoff,
        )
        result = await self.session.execute(query)
        removed = 0
        for run in result.scalars().all():
            if run.id in keep_ids:
                continue
            await self.session.delete(run)
            removed += 1
        await self.session.flush()
        return removed


__all__ = [
    "OrderEventRepository",
    "OrderItemRepository",
    "OrderRepository",
    "OrderSyncRunRepository",
    "ShipmentRepository",
    "TrackingEventRepository",
]
