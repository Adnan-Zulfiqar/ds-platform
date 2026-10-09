"""Subscription rows (Track E6). Tenant-scoped like every business table."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    FeatureFlag,
    TenantFeatureFlag,
    TenantSubscription,
    TrialFingerprint,
)
from app.repositories.base import BaseRepository, TenantScopedRepository


class TenantSubscriptionRepository(TenantScopedRepository[TenantSubscription]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, TenantSubscription)

    async def current(self, *, lock: bool = False) -> TenantSubscription | None:
        query = self._base_query()
        if lock:
            query = query.with_for_update()
        return (await self.session.execute(query)).scalar_one_or_none()


class TrialFingerprintRegistry:
    """Unscoped by necessity: the question is "did *any* workspace already
    get a trial with this store?" (CLAUDE.md §4, owner requirement
    2026-10-04). It takes a hash and returns a tenant id, never a row, and it
    runs only in the ``billing.claim_trial`` Celery task, not on a request
    path."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, fingerprint: str, tenant_id: uuid.UUID) -> bool:
        """Record the fingerprint for ``tenant_id``. True when the store had
        already given a trial to a *different* workspace, including one since
        deleted (whose id the foreign key has cleared to NULL)."""
        inserted = await self.session.execute(
            insert(TrialFingerprint)
            .values(id=uuid.uuid4(), fingerprint=fingerprint, first_tenant_id=tenant_id)
            .on_conflict_do_nothing(index_elements=["fingerprint"])
            .returning(TrialFingerprint.id)
        )
        if inserted.scalar_one_or_none() is not None:
            return False  # first use of this store
        holder = await self.session.execute(
            select(TrialFingerprint.first_tenant_id).where(
                TrialFingerprint.fingerprint == fingerprint
            )
        )
        return holder.scalar_one_or_none() != tenant_id


__all__ = ["TenantSubscriptionRepository", "TrialFingerprintRegistry"]


class FeatureFlagRepository(BaseRepository[FeatureFlag]):
    """Platform reference data, like roles (CLAUDE.md §4): the switches and
    their platform-wide defaults. No tenant owns a row."""

    sortable_fields = frozenset({"created_at", "key"})
    default_sort_field = "key"

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, FeatureFlag)

    async def by_key(self, key: str) -> FeatureFlag | None:
        query = select(FeatureFlag).where(FeatureFlag.key == key)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def all_flags(self) -> list[FeatureFlag]:
        query = select(FeatureFlag).order_by(FeatureFlag.key)
        return list((await self.session.execute(query)).scalars().all())


class TenantFeatureFlagRepository(TenantScopedRepository[TenantFeatureFlag]):
    """One workspace's overrides."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, TenantFeatureFlag)

    async def by_key(self, key: str) -> TenantFeatureFlag | None:
        query = self._base_query().where(TenantFeatureFlag.key == key)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def all_overrides(self) -> list[TenantFeatureFlag]:
        query = self._base_query().order_by(TenantFeatureFlag.key)
        return list((await self.session.execute(query)).scalars().all())
