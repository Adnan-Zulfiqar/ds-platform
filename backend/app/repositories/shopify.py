"""Shopify connection and store listing repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.models.shopify import ShopifyConnection, StoreListing
from app.repositories.base import BaseRepository, TenantScopedRepository


class ShopifyConnectionRepository(TenantScopedRepository[ShopifyConnection]):
    """Tenant-scoped Shopify connections.

    ``ShopifyConnection`` uses ``IdentifiedBase`` rather than soft-delete, so
    this repository overrides ``_base_query`` to filter by tenant only.
    """

    sortable_fields = frozenset({"created_at", "updated_at", "status", "last_sync_at"})
    searchable_fields = frozenset({"shop_domain"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ShopifyConnection)

    def _base_query(self) -> Any:
        tenant_id = require_tenant_id()
        return select(ShopifyConnection).where(ShopifyConnection.tenant_id == tenant_id)

    async def list_all(self) -> Sequence[ShopifyConnection]:
        result = await self.session.execute(self._base_query())
        return list(result.scalars().all())

    async def get_by_store(self, store_id: uuid.UUID) -> ShopifyConnection | None:
        result = await self.session.execute(
            self._base_query().where(ShopifyConnection.store_id == store_id)
        )
        return result.scalar_one_or_none()

    async def get_by_shop_domain(self, shop_domain: str) -> ShopifyConnection | None:
        result = await self.session.execute(
            self._base_query().where(ShopifyConnection.shop_domain == shop_domain)
        )
        return result.scalar_one_or_none()


class StoreListingRepository(TenantScopedRepository[StoreListing]):
    sortable_fields = frozenset({"created_at", "updated_at", "last_synced_at"})
    searchable_fields = frozenset({"external_product_id"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StoreListing)

    async def get_for_product(
        self, *, store_id: uuid.UUID, product_id: uuid.UUID
    ) -> StoreListing | None:
        result = await self.session.execute(
            self._base_query().where(
                StoreListing.store_id == store_id,
                StoreListing.product_id == product_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_store(self, store_id: uuid.UUID) -> Sequence[StoreListing]:
        result = await self.session.execute(
            self._base_query().where(StoreListing.store_id == store_id)
        )
        return list(result.scalars().all())

    async def list_for_product(self, product_id: uuid.UUID) -> Sequence[StoreListing]:
        result = await self.session.execute(
            self._base_query().where(StoreListing.product_id == product_id)
        )
        return list(result.scalars().all())


class ShopifyMaintenanceRepository(BaseRepository[ShopifyConnection]):
    """Unscoped sweep / webhook lookup — documented unscoped repository.

    HTTP handlers must never use this class for ordinary CRUD. Celery tasks and
    inbound Shopify webhooks bind tenant context per row after an unscoped
    domain lookup.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ShopifyConnection)

    async def list_connected(self, *, limit: int = 500) -> Sequence[ShopifyConnection]:
        from app.models.integration import IntegrationStatus

        query = (
            select(ShopifyConnection)
            .where(ShopifyConnection.status == IntegrationStatus.CONNECTED)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_shop_domain(self, shop_domain: str) -> ShopifyConnection | None:
        """Any status — used to reject connect when another tenant owns the shop."""
        result = await self.session.execute(
            select(ShopifyConnection).where(ShopifyConnection.shop_domain == shop_domain)
        )
        return result.scalar_one_or_none()

    async def get_connected_by_shop_domain(self, shop_domain: str) -> ShopifyConnection | None:
        """Indexed domain lookup for HMAC-verified webhooks — not a table scan."""
        from app.models.integration import IntegrationStatus

        result = await self.session.execute(
            select(ShopifyConnection).where(
                ShopifyConnection.shop_domain == shop_domain,
                ShopifyConnection.status == IntegrationStatus.CONNECTED,
            )
        )
        return result.scalar_one_or_none()
