"""Resolve the AliExpress ship-to country for an import.

Priority (documented in ``docs/ALIEXPRESS_INTEGRATION.md``):

1. Explicit request ``ship_to_country``
2. Selected store's ``settings.countryCode`` / ``settings.country`` when present
3. Platform ``DEFAULT_SHIP_TO_COUNTRY`` when configured
4. Tenant's last successful import destination
5. Otherwise require the merchant to choose — never invent ``US``
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.integrations.aliexpress.countries import normalise_country_code
from app.models.product import ImportStatus, ProductImport, ProductSource
from app.repositories.store import StoreRepository
from app.services.base import BaseService


class ImportDestinationService(BaseService):
    """Picks a ship-to country without silently defaulting every merchant to US."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.stores = StoreRepository(session)

    async def resolve(
        self,
        *,
        ship_to_country: str | None,
        store_id: uuid.UUID | None = None,
    ) -> str:
        explicit = normalise_country_code(ship_to_country)
        if explicit:
            return explicit

        if store_id is not None:
            store = await self.stores.get_by_id(store_id)
            if store is not None:
                from_store = country_from_store_settings(store.settings)
                if from_store:
                    return from_store

        configured = normalise_country_code(settings.default_ship_to_country)
        if configured:
            return configured

        last = await self._last_successful_ship_to()
        if last:
            return last

        raise ValidationError(
            "Select a ship-to country. AliExpress availability and price depend "
            "on destination, and this workspace has no default configured.",
            details={"field": "ship_to_country"},
        )

    async def _last_successful_ship_to(self) -> str | None:
        query = (
            select(ProductImport.ship_to_country)
            .where(
                ProductImport.source == ProductSource.ALIEXPRESS,
                ProductImport.status == ImportStatus.SUCCEEDED,
                ProductImport.ship_to_country.is_not(None),
            )
            .order_by(ProductImport.finished_at.desc().nullslast())
            .limit(1)
        )
        # TenantScopedRepository applies tenant via model queries; raw select
        # needs the same predicate from context.
        from app.core.context import require_tenant_id

        query = query.where(ProductImport.tenant_id == require_tenant_id())
        result = await self.session.execute(query)
        return normalise_country_code(result.scalar_one_or_none())


def country_from_store_settings(settings_bag: dict[str, Any] | None) -> str | None:
    if not settings_bag:
        return None
    for key in ("countryCode", "country_code", "country", "shipToCountry", "ship_to_country"):
        raw = settings_bag.get(key)
        found = normalise_country_code(str(raw) if raw else None)
        if found:
            return found
    return None


__all__ = ["ImportDestinationService", "country_from_store_settings"]
