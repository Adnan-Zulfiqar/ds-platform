"""Store data access — tenant-scoped sales channels."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store import Store, StoreStatus
from app.repositories.base import TenantScopedRepository


class StoreRepository(TenantScopedRepository[Store]):
    """Reads and writes for sales-channel stores."""

    sortable_fields = frozenset(
        {
            "created_at",
            "updated_at",
            "name",
            "status",
            "platform",
            "last_sync_at",
            "last_activity_at",
            "health_score",
        }
    )
    searchable_fields = frozenset({"name", "slug", "external_store_id"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Store)

    async def get_by_slug(self, slug: str) -> Store | None:
        query = self._base_query().where(Store.slug == slug)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def count_by_status(self) -> dict[str, int]:
        where_clause = self._base_query().whereclause
        query = select(Store.status, func.count(Store.id)).select_from(Store)
        if where_clause is not None:
            query = query.where(where_clause)
        result = await self.session.execute(query.group_by(Store.status))
        return {str(status.value): count for status, count in result.all()}

    async def list_connected(self) -> list[Store]:
        query = self._base_query().where(Store.status == StoreStatus.CONNECTED)
        result = await self.session.execute(query)
        return list(result.scalars().all())
