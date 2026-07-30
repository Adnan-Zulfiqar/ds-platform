"""Tenant repository.

Deliberately extends :class:`BaseRepository`, not :class:`TenantScopedRepository`.
The tenants table sits above the tenancy boundary — scoping it to the current
tenant would make it impossible to resolve a tenant during request setup, which
is the step that establishes that context in the first place.

Because this repository is unscoped, it is only ever called from tenant
resolution and from platform-administration code paths. It must never be exposed
through a customer-facing endpoint.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.repositories.base import BaseRepository


class TenantRepository(BaseRepository[Tenant]):
    sortable_fields = frozenset({"created_at", "updated_at", "name", "slug", "status"})
    searchable_fields = frozenset({"name", "slug"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Tenant)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Resolve a tenant by its subdomain label.

        On the hot path for every request when tenants are addressed by
        subdomain, so ``slug`` is indexed and unique. This result is a strong
        candidate for a short-TTL cache once traffic justifies it.
        """
        query = self._base_query().where(Tenant.slug == slug.lower())
        return (await self.session.execute(query)).scalar_one_or_none()

    async def slug_exists(self, slug: str) -> bool:
        """Check slug availability.

        Queries without the soft-delete filter on purpose: a deleted tenant
        still owns its subdomain, because reissuing it would route the previous
        customer's stale links and webhooks to a different account.
        """
        query = select(Tenant.id).where(Tenant.slug == slug.lower()).limit(1)
        return (await self.session.execute(query)).scalar_one_or_none() is not None


__all__ = ["TenantRepository"]
