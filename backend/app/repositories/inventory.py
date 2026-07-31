"""Inventory sync history data access."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryChange, InventorySyncRun
from app.models.order import SyncRunStatus
from app.repositories.base import TenantScopedRepository
from app.schemas.common import ListQueryParams


class InventorySyncRunRepository(TenantScopedRepository[InventorySyncRun]):
    sortable_fields = frozenset({"created_at", "updated_at", "started_at", "finished_at"})
    searchable_fields = frozenset({"error_code", "error_message"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InventorySyncRun)

    async def count_failed_since(self, *, since: datetime) -> int:
        where_clause = self._base_query().whereclause
        query = (
            select(func.count(InventorySyncRun.id))
            .select_from(InventorySyncRun)
            .where(
                InventorySyncRun.status == SyncRunStatus.FAILED,
                InventorySyncRun.created_at >= since,
            )
        )
        if where_clause is not None:
            query = query.where(where_clause)
        return int((await self.session.execute(query)).scalar_one())


class InventoryChangeRepository(TenantScopedRepository[InventoryChange]):
    sortable_fields = frozenset({"created_at", "updated_at", "new_quantity", "previous_quantity"})
    searchable_fields = frozenset({"note"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, InventoryChange)

    async def list_for_product(
        self, product_id: uuid.UUID, *, params: ListQueryParams
    ) -> tuple[list[InventoryChange], int]:
        rows, total = await self.list(params, filters={"product_id": product_id})
        return list(rows), total
