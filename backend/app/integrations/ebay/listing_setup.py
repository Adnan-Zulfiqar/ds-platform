"""EBAY-C2: what a workspace will list with on eBay.

Reads the seller's live policies and locations, stores the admin's choice of
defaults per marketplace, and creates a warehouse location when the seller has
none. See ``docs/ebay/EBAY_C2_LISTING_SETUP.md``.

Every token comes from ``EbayConnectionService.access_token_for``; a revoked
grant surfaces as ``EbayTokenRevokedError`` and the connection is marked for
reconnection there, not here.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.integrations.ebay.connection import EbayConnectionService
from app.integrations.ebay.exceptions import (
    EbayNotConnectedError,
    EbayPolicyNotFoundError,
    EbayTokenRevokedError,
)
from app.integrations.ebay.seller_setup import (
    EBAY_SUPPORTED_MARKETPLACES,
    EbayBusinessPolicies,
    EbayInventoryLocation,
    EbaySellerClient,
    NewInventoryLocation,
)
from app.models.ebay import EbayConnection, EbayConnectionStatus, EbayListingDefaults
from app.repositories.ebay import EbayListingDefaultsRepository

logger = get_logger(__name__)

#: eBay's limit for ``merchantLocationKey`` is 36 characters.
_LOCATION_KEY_PREFIX = "droppilot-"


@dataclass(frozen=True, slots=True)
class ListingSetup:
    marketplace_id: str
    business_policies_enabled: bool
    policies: EbayBusinessPolicies | None
    locations: tuple[EbayInventoryLocation, ...]
    defaults: EbayListingDefaults | None


@dataclass(frozen=True, slots=True)
class ListingDefaultsChoice:
    marketplace_id: str
    fulfillment_policy_id: str
    payment_policy_id: str
    return_policy_id: str
    merchant_location_key: str


class EbayListingSetupService:
    def __init__(self, session: AsyncSession) -> None:
        self.connections = EbayConnectionService(session)
        self.defaults = EbayListingDefaultsRepository(session)

    async def get_setup(self, marketplace_id: str) -> ListingSetup:
        _require_supported(marketplace_id)
        connection, client = await self._client()
        enabled = await client.business_policies_enabled()
        # Without the business-policies programme eBay refuses the policy
        # calls outright; asking anyway would turn a setup state into an error.
        policies = await client.business_policies(marketplace_id) if enabled else None
        locations = await client.inventory_locations()
        defaults = await self.defaults.get_for_marketplace(marketplace_id)
        if defaults is not None and defaults.connection_id != connection.id:
            defaults = None  # pragma: no cover - cascade removes rows of an old connection
        return ListingSetup(
            marketplace_id=marketplace_id,
            business_policies_enabled=enabled,
            policies=policies,
            locations=locations,
            defaults=defaults,
        )

    async def save_defaults(self, choice: ListingDefaultsChoice) -> EbayListingDefaults:
        """Store the choice after checking each id against the seller's live
        lists. A stale id is refused rather than stored for C3 to trip over."""
        _require_supported(choice.marketplace_id)
        connection, client = await self._client()
        if not await client.business_policies_enabled():
            raise EbayPolicyNotFoundError(
                "This eBay account has not enabled business policies yet."
            )
        policies = await client.business_policies(choice.marketplace_id)
        locations = await client.inventory_locations()

        checks = (
            (choice.fulfillment_policy_id, {p.id for p in policies.fulfillment}),
            (choice.payment_policy_id, {p.id for p in policies.payment}),
            (choice.return_policy_id, {p.id for p in policies.returns}),
            (choice.merchant_location_key, {loc.key for loc in locations if loc.enabled}),
        )
        if any(value not in allowed for value, allowed in checks):
            raise EbayPolicyNotFoundError()

        values = {
            "connection_id": connection.id,
            "fulfillment_policy_id": choice.fulfillment_policy_id,
            "payment_policy_id": choice.payment_policy_id,
            "return_policy_id": choice.return_policy_id,
            "merchant_location_key": choice.merchant_location_key,
        }
        existing = await self.defaults.get_for_marketplace(choice.marketplace_id)
        if existing is None:
            saved = await self.defaults.create(marketplace_id=choice.marketplace_id, **values)
        else:
            saved = await self.defaults.update(existing, **values)
        logger.info("ebay_listing_defaults_saved", marketplace_id=choice.marketplace_id)
        return saved

    async def create_location(self, location: NewInventoryLocation) -> EbayInventoryLocation:
        if not (location.postal_code or (location.city and location.state_or_province)):
            raise ValidationError(
                "Give a postal code, or a city together with its state or province."
            )
        _, client = await self._client()
        key = f"{_LOCATION_KEY_PREFIX}{secrets.token_hex(8)}"
        await client.create_inventory_location(key, location)
        logger.info("ebay_inventory_location_created")
        return EbayInventoryLocation(
            key=key,
            name=location.name,
            city=location.city,
            postal_code=location.postal_code,
            country=location.country,
            enabled=True,
        )

    async def _client(self) -> tuple[EbayConnection, EbaySellerClient]:
        connection = await self.connections.get_connection()
        if connection is None or connection.status is EbayConnectionStatus.PENDING:
            raise EbayNotConnectedError()
        if connection.needs_reconnect:
            raise EbayTokenRevokedError()
        token = await self.connections.access_token_for(connection)
        return connection, EbaySellerClient(token)


def _require_supported(marketplace_id: str) -> None:
    if marketplace_id not in EBAY_SUPPORTED_MARKETPLACES:
        raise ValidationError(
            "That eBay marketplace is not supported.",
            details={"supported": list(EBAY_SUPPORTED_MARKETPLACES)},
        )


__all__ = [
    "EbayListingSetupService",
    "ListingDefaultsChoice",
    "ListingSetup",
]
