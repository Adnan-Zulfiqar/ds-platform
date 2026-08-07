"""Resolve the AliExpress ship-to country and target currency for an import.

Ship-to priority (documented in ``docs/ALIEXPRESS_INTEGRATION.md``):

1. Explicit request ``ship_to_country``
2. Selected store's ``settings.countryCode`` / ``settings.country`` when present
3. Platform ``DEFAULT_SHIP_TO_COUNTRY`` when configured
4. Tenant's last successful import destination
5. Otherwise require the merchant to choose — never invent ``US``

Currency priority (M24B) is the same shape, deliberately: a merchant must not
have to know AliExpress's ``target_currency`` parameter exists, and DropPilot
must not silently ask AliExpress for USD pricing on a GB-destined import just
because nothing overrode a hardcoded default (M24A's live-traced bug — see
``docs/ALIEXPRESS_LOCALIZED_PRICING.md``).

1. Explicit request ``currency``
2. Selected store's *verified* currency (``Store.currency_last_synced_at`` set
   — an unsynced store's currency is untrusted, same M24A authority rule
   ``PricingEngine._resolve_selling_currency`` already enforces)
3. The resolved ship-to country's mapped currency (``CURRENCY_BY_COUNTRY`` —
   GB/US only for now)
4. Tenant's ``default_currency``
5. Otherwise require the merchant to choose — never invent USD
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.domain.money import normalise_currency
from app.integrations.aliexpress.countries import currency_for_country, normalise_country_code
from app.models.product import ImportStatus, ProductImport, ProductSource
from app.models.store import StorePlatform
from app.repositories.store import StoreRepository
from app.repositories.tenant import TenantRepository
from app.services.base import BaseService


class ImportDestinationService(BaseService):
    """Picks a ship-to country and target currency without silently defaulting."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.stores = StoreRepository(session)
        self.tenants = TenantRepository(session)

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

    async def resolve_currency(
        self,
        *,
        currency: str | None,
        ship_to_country: str,
        store_id: uuid.UUID | None = None,
    ) -> tuple[str, str]:
        """Resolve the ``target_currency`` to request from AliExpress.

        Returns ``(currency, source)`` — ``source`` is one of ``explicit``,
        ``verified_store``, ``destination``, ``workspace`` — persisted on the
        product/import record so a later "why is this GBP" question has a real
        answer instead of a guess.
        """
        if currency:
            try:
                return normalise_currency(currency), "explicit"
            except ValidationError:
                pass  # Fall through rather than reject an otherwise-fine import.

        if store_id is not None:
            store = await self.stores.get_by_id(store_id)
            if (
                store is not None
                and store.platform is StorePlatform.SHOPIFY
                and store.currency_last_synced_at is not None
                and store.currency
            ):
                try:
                    return normalise_currency(store.currency), "verified_store"
                except ValidationError:
                    pass

        from_destination = currency_for_country(ship_to_country)
        if from_destination:
            return from_destination, "destination"

        from app.core.context import require_tenant_id

        tenant = await self.tenants.get_by_id(require_tenant_id())
        if tenant is not None and getattr(tenant, "default_currency", None):
            try:
                return normalise_currency(tenant.default_currency), "workspace"
            except ValidationError:
                pass

        raise ValidationError(
            "Select a target currency. This destination has no mapped market "
            "currency and this workspace has no default configured.",
            details={"field": "currency", "ship_to_country": ship_to_country},
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
