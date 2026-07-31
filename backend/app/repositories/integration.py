"""Integration connection repository.

Tenant-scoped, so every query is confined to the caller's workspace
automatically. That matters more here than almost anywhere else in the
codebase: a leaked row does not expose a product listing, it exposes another
company's supplier credentials.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import AliExpressConnection, IntegrationStatus
from app.repositories.base import BaseRepository, TenantScopedRepository


class AliExpressConnectionRepository(TenantScopedRepository[AliExpressConnection]):
    """Tenant-scoped access to AliExpress connections."""

    sortable_fields = frozenset({"created_at", "updated_at", "status", "last_sync_at"})
    searchable_fields = frozenset({"app_key"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AliExpressConnection)

    async def get_for_tenant(self) -> AliExpressConnection | None:
        """Return the current tenant's connection, if any.

        Takes no tenant argument: the filter comes from bound context, so there
        is no parameter a caller could pass wrongly.

        Returns ``None`` rather than raising, because "not connected" is an
        ordinary state that the status endpoint reports and the UI renders — not
        an error.
        """
        result = await self.session.execute(self._base_query())
        return result.scalar_one_or_none()


class IntegrationMaintenanceRepository(BaseRepository[AliExpressConnection]):
    """Unscoped access for scheduled maintenance.

    **Deliberately not tenant-scoped**, and the third such repository in the
    codebase — the others are documented in ``tenant.py`` and ``user.py``.

    A background health check runs on no tenant's behalf: it sweeps every
    connection across every workspace looking for tokens near expiry. A scoped
    repository cannot express that, because there is no single tenant to bind.

    **The rule:** the only callers are Celery tasks in
    ``app.tasks.integrations``. Those tasks bind tenant context per connection
    before doing anything with one, so the work itself remains scoped even
    though the sweep is not. Nothing reachable from an HTTP request may use this
    class.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AliExpressConnection)

    async def find_expiring(
        self, *, within_seconds: int, limit: int = 500
    ) -> Sequence[AliExpressConnection]:
        """Connections whose access token expires within the window.

        Includes rows with no recorded expiry: an unknown expiry is treated as
        expired throughout this integration, because acting on a token of
        unknown validity risks a sync failing in a way that looks like an
        outage.

        Bounded by ``limit`` so a sweep cannot load an unbounded result set into
        a worker.
        """
        deadline = datetime.now(UTC) + timedelta(seconds=within_seconds)

        query = (
            select(AliExpressConnection)
            .where(
                AliExpressConnection.status == IntegrationStatus.CONNECTED,
                (AliExpressConnection.token_expiry.is_(None))
                | (AliExpressConnection.token_expiry <= deadline),
            )
            .order_by(AliExpressConnection.token_expiry.asc().nulls_first())
            .limit(limit)
        )
        return (await self.session.execute(query)).scalars().all()

    async def get_by_id_unscoped(self, connection_id: uuid.UUID) -> AliExpressConnection | None:
        """Fetch one connection without a tenant filter.

        For a task that has already selected a connection from a sweep and needs
        to reload it inside its own transaction.
        """
        query = select(AliExpressConnection).where(AliExpressConnection.id == connection_id)
        return (await self.session.execute(query)).scalar_one_or_none()


__all__ = ["AliExpressConnectionRepository", "IntegrationMaintenanceRepository"]
