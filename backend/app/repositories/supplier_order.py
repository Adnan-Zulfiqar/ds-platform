"""Supplier orders and fulfilment settings (Track F). Tenant-scoped like
every business table; no unscoped access is needed, because every caller
already runs inside one workspace's context."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier_order import FulfilmentSettings, SupplierOrder, SupplierOrderStatus
from app.repositories.base import TenantScopedRepository


class SupplierOrderRepository(TenantScopedRepository[SupplierOrder]):
    sortable_fields = frozenset({"created_at", "updated_at", "placed_at"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, SupplierOrder)

    async def for_order(self, order_id: uuid.UUID, *, lock: bool = False) -> SupplierOrder | None:
        query = self._base_query().where(SupplierOrder.order_id == order_id)
        if lock:
            query = query.with_for_update()
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_locked(self, supplier_order_id: uuid.UUID) -> SupplierOrder | None:
        query = self._base_query().where(SupplierOrder.id == supplier_order_id).with_for_update()
        return (await self.session.execute(query)).scalar_one_or_none()

    async def awaiting_tracking(self, *, limit: int = 200) -> Sequence[SupplierOrder]:
        """Placed orders with no tracking pushed yet, least recently checked first."""
        query = (
            self._base_query()
            .where(
                SupplierOrder.status == SupplierOrderStatus.PLACED.value,
                SupplierOrder.tracking_pushed_at.is_(None),
            )
            .order_by(SupplierOrder.last_checked_at.asc().nulls_first())
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())


class FulfilmentSettingsRepository(TenantScopedRepository[FulfilmentSettings]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, FulfilmentSettings)

    async def current(self) -> FulfilmentSettings | None:
        return (await self.session.execute(self._base_query())).scalar_one_or_none()


__all__ = ["FulfilmentSettingsRepository", "SupplierOrderRepository"]
