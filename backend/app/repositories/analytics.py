"""Daily analytics rollups."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsDaily
from app.repositories.base import TenantScopedRepository


class AnalyticsDailyRepository(TenantScopedRepository[AnalyticsDaily]):
    sortable_fields = frozenset({"created_at", "day", "revenue", "order_count"})
    searchable_fields = frozenset()
    default_sort_field = "day"

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AnalyticsDaily)

    async def get_for_day(self, *, day: date, store_id: uuid.UUID | None) -> AnalyticsDaily | None:
        query = self._base_query().where(AnalyticsDaily.day == day)
        if store_id is None:
            query = query.where(AnalyticsDaily.store_id.is_(None))
        else:
            query = query.where(AnalyticsDaily.store_id == store_id)
        result = await self.session.execute(query.limit(1))
        return result.scalars().first()

    async def list_between(
        self, *, start: date, end: date, store_id: uuid.UUID | None = None
    ) -> list[AnalyticsDaily]:
        query = self._base_query().where(
            AnalyticsDaily.day >= start,
            AnalyticsDaily.day <= end,
        )
        if store_id is None:
            query = query.where(AnalyticsDaily.store_id.is_(None))
        else:
            query = query.where(AnalyticsDaily.store_id == store_id)
        query = query.order_by(AnalyticsDaily.day.asc())
        result = await self.session.execute(query)
        return list(result.scalars().all())
