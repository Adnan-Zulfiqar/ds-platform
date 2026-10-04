"""Subscription rows (Track E6). Tenant-scoped like every business table."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import TenantSubscription
from app.repositories.base import TenantScopedRepository


class TenantSubscriptionRepository(TenantScopedRepository[TenantSubscription]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, TenantSubscription)

    async def current(self, *, lock: bool = False) -> TenantSubscription | None:
        query = self._base_query()
        if lock:
            query = query.with_for_update()
        return (await self.session.execute(query)).scalar_one_or_none()


__all__ = ["TenantSubscriptionRepository"]
