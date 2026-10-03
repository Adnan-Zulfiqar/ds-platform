"""The seller's own setup on eBay: business policies and inventory locations.

EBAY-C2's outbound calls. Each takes an access token obtained from
``EbayConnectionService.access_token_for`` — never decrypts one itself — and
maps 401/403 to ``EbayTokenRevokedError`` exactly as the identity call does,
so a revoked grant sends the merchant back through consent instead of
failing forever.

Parsers keep only what a merchant chooses between (id, name, and for a
location its city/postcode/country). Policy bodies carry shipping costs and
return terms that C3 does not need from us — eBay applies them itself once an
offer names the policy.

No retries: these are interactive reads behind a settings panel, and a
merchant clicking again is cheaper than holding a request open through
backoff.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ebay.exceptions import (
    EbayListingRejectedError,
    EbaySellerApiUnavailableError,
    EbayTokenRevokedError,
)

logger = get_logger(__name__)

#: Marketplaces a merchant can configure. C3 must price and publish for each
#: one listed, so this grows only when C3 does.
EBAY_SUPPORTED_MARKETPLACES: tuple[str, ...] = (
    "EBAY_US",
    "EBAY_GB",
    "EBAY_DE",
    "EBAY_AU",
    "EBAY_CA",
    "EBAY_FR",
    "EBAY_IT",
    "EBAY_ES",
)


@dataclass(frozen=True, slots=True)
class EbayMarketplace:
    """What EBAY-C3 needs to list on one marketplace.

    ``currency`` is fixed per marketplace (D-C3-4): an eBay site prices in
    one currency, unlike a Shopify shop whose currency can change.
    """

    id: str
    name: str
    country: str
    currency: str
    content_language: str
    site_host: str


EBAY_MARKETPLACES: dict[str, EbayMarketplace] = {
    m.id: m
    for m in (
        EbayMarketplace("EBAY_US", "United States", "US", "USD", "en-US", "www.ebay.com"),
        EbayMarketplace("EBAY_GB", "United Kingdom", "GB", "GBP", "en-GB", "www.ebay.co.uk"),
        EbayMarketplace("EBAY_DE", "Germany", "DE", "EUR", "de-DE", "www.ebay.de"),
        EbayMarketplace("EBAY_AU", "Australia", "AU", "AUD", "en-AU", "www.ebay.com.au"),
        EbayMarketplace("EBAY_CA", "Canada", "CA", "CAD", "en-CA", "www.ebay.ca"),
        EbayMarketplace("EBAY_FR", "France", "FR", "EUR", "fr-FR", "www.ebay.fr"),
        EbayMarketplace("EBAY_IT", "Italy", "IT", "EUR", "it-IT", "www.ebay.it"),
        EbayMarketplace("EBAY_ES", "Spain", "ES", "EUR", "es-ES", "www.ebay.es"),
    )
}
assert tuple(EBAY_MARKETPLACES) == EBAY_SUPPORTED_MARKETPLACES

_BUSINESS_POLICIES_PROGRAM = "SELLING_POLICY_MANAGEMENT"
#: eBay's page-size ceiling for getInventoryLocations.
_LOCATION_PAGE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class EbayOffer:
    offer_id: str
    published: bool
    listing_id: str | None


@dataclass(frozen=True, slots=True)
class EbayPolicy:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class EbayBusinessPolicies:
    fulfillment: tuple[EbayPolicy, ...]
    payment: tuple[EbayPolicy, ...]
    returns: tuple[EbayPolicy, ...]


@dataclass(frozen=True, slots=True)
class EbayInventoryLocation:
    key: str
    name: str | None
    city: str | None
    postal_code: str | None
    country: str | None
    enabled: bool


@dataclass(frozen=True, slots=True)
class NewInventoryLocation:
    """A warehouse address. eBay requires a country plus a postal code, or a
    city with its state/province."""

    name: str
    address_line1: str | None
    city: str | None
    state_or_province: str | None
    postal_code: str | None
    country: str


def _text(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_policies(payload: Any, *, list_key: str, id_key: str) -> tuple[EbayPolicy, ...]:
    """Pull ``{id, name}`` pairs out of a get*Policies response.

    Entries without an id are dropped rather than failing the whole list: one
    malformed policy should not hide the seller's usable ones.
    """
    if not isinstance(payload, Mapping):
        raise EbaySellerApiUnavailableError("eBay returned a malformed policy list.")
    items = payload.get(list_key) or []
    if not isinstance(items, list):
        raise EbaySellerApiUnavailableError("eBay returned a malformed policy list.")
    policies: list[EbayPolicy] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        policy_id = _text(item, id_key)
        if policy_id is None:
            continue
        policies.append(EbayPolicy(id=policy_id, name=_text(item, "name") or policy_id))
    return tuple(policies)


def parse_locations(payload: Any) -> tuple[EbayInventoryLocation, ...]:
    if not isinstance(payload, Mapping):
        raise EbaySellerApiUnavailableError("eBay returned a malformed location list.")
    items = payload.get("locations") or []
    if not isinstance(items, list):
        raise EbaySellerApiUnavailableError("eBay returned a malformed location list.")
    locations: list[EbayInventoryLocation] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        key = _text(item, "merchantLocationKey")
        if key is None:
            continue
        location = item.get("location")
        address = location.get("address") if isinstance(location, Mapping) else None
        address = address if isinstance(address, Mapping) else {}
        locations.append(
            EbayInventoryLocation(
                key=key,
                name=_text(item, "name"),
                city=_text(address, "city"),
                postal_code=_text(address, "postalCode"),
                country=_text(address, "country"),
                enabled=(_text(item, "merchantLocationStatus") or "ENABLED") == "ENABLED",
            )
        )
    return tuple(locations)


def parse_opted_in(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        raise EbaySellerApiUnavailableError("eBay returned a malformed program list.")
    programs = payload.get("programs") or []
    if not isinstance(programs, list):
        return False
    return any(
        isinstance(p, Mapping) and p.get("programType") == _BUSINESS_POLICIES_PROGRAM
        for p in programs
    )


class EbaySellerClient:
    """Thin wrapper over the Account and Inventory API calls C2 makes."""

    def __init__(self, access_token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        self._base = settings.ebay.notification_api_base
        self._timeout = httpx.Timeout(
            settings.ebay.request_timeout_seconds,
            connect=settings.ebay.connect_timeout_seconds,
        )

    async def business_policies_enabled(self) -> bool:
        body = await self._get("/sell/account/v1/program/get_opted_in_programs", call="programs")
        return parse_opted_in(body)

    async def business_policies(self, marketplace_id: str) -> EbayBusinessPolicies:
        params = {"marketplace_id": marketplace_id}
        fulfillment = await self._get(
            "/sell/account/v1/fulfillment_policy", call="fulfillment_policies", params=params
        )
        payment = await self._get(
            "/sell/account/v1/payment_policy", call="payment_policies", params=params
        )
        returns = await self._get(
            "/sell/account/v1/return_policy", call="return_policies", params=params
        )
        return EbayBusinessPolicies(
            fulfillment=parse_policies(
                fulfillment, list_key="fulfillmentPolicies", id_key="fulfillmentPolicyId"
            ),
            payment=parse_policies(payment, list_key="paymentPolicies", id_key="paymentPolicyId"),
            returns=parse_policies(returns, list_key="returnPolicies", id_key="returnPolicyId"),
        )

    async def inventory_locations(self) -> tuple[EbayInventoryLocation, ...]:
        body = await self._get(
            "/sell/inventory/v1/location",
            call="inventory_locations",
            params={"limit": str(_LOCATION_PAGE_LIMIT), "offset": "0"},
        )
        return parse_locations(body)

    async def create_inventory_location(self, key: str, location: NewInventoryLocation) -> None:
        address: dict[str, str] = {"country": location.country}
        for field, value in (
            ("addressLine1", location.address_line1),
            ("city", location.city),
            ("stateOrProvince", location.state_or_province),
            ("postalCode", location.postal_code),
        ):
            if value:
                address[field] = value
        payload = {
            "name": location.name,
            "location": {"address": address},
            "locationTypes": ["WAREHOUSE"],
            "merchantLocationStatus": "ENABLED",
        }
        response = await self._request(
            "POST",
            f"/sell/inventory/v1/location/{key}",
            call="create_inventory_location",
            json=payload,
        )
        if response.status_code not in (httpx.codes.NO_CONTENT, httpx.codes.OK):
            self._raise_for(response, call="create_inventory_location")

    # --- EBAY-C3: inventory item, offer, publish ---------------------------

    async def put_inventory_item(
        self, sku: str, payload: Mapping[str, Any], *, content_language: str
    ) -> None:
        """createOrReplaceInventoryItem — idempotent by SKU."""
        response = await self._request(
            "PUT",
            f"/sell/inventory/v1/inventory_item/{quote(sku, safe='')}",
            call="put_inventory_item",
            json=payload,
            content_language=content_language,
        )
        if response.status_code not in (httpx.codes.NO_CONTENT, httpx.codes.OK):
            self._raise_for_listing(response, call="put_inventory_item")

    async def find_offer(self, sku: str, marketplace_id: str) -> EbayOffer | None:
        """The offer already made for this SKU on this marketplace, if any —
        what lets a retried publish adopt instead of duplicating."""
        response = await self._request(
            "GET",
            "/sell/inventory/v1/offer",
            call="get_offers",
            params={"sku": sku, "marketplace_id": marketplace_id},
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code != httpx.codes.OK:
            self._raise_for_listing(response, call="get_offers")
        try:
            body = response.json()
        except ValueError as exc:
            raise EbaySellerApiUnavailableError("eBay returned a non-JSON response.") from exc
        offers = body.get("offers") if isinstance(body, Mapping) else None
        for offer in offers if isinstance(offers, list) else []:
            if isinstance(offer, Mapping) and _text(offer, "offerId"):
                listing = offer.get("listing")
                listing_id = _text(listing, "listingId") if isinstance(listing, Mapping) else None
                return EbayOffer(
                    offer_id=str(_text(offer, "offerId")),
                    published=_text(offer, "status") == "PUBLISHED",
                    listing_id=listing_id,
                )
        return None

    async def create_offer(self, payload: Mapping[str, Any], *, content_language: str) -> str:
        response = await self._request(
            "POST",
            "/sell/inventory/v1/offer",
            call="create_offer",
            json=payload,
            content_language=content_language,
        )
        if response.status_code not in (httpx.codes.CREATED, httpx.codes.OK):
            self._raise_for_listing(response, call="create_offer")
        offer_id = _text(response.json(), "offerId")
        if offer_id is None:
            raise EbaySellerApiUnavailableError("eBay created an offer without an id.")
        return offer_id

    async def update_offer(
        self, offer_id: str, payload: Mapping[str, Any], *, content_language: str
    ) -> None:
        """updateOffer. On a published offer eBay updates the live listing."""
        response = await self._request(
            "PUT",
            f"/sell/inventory/v1/offer/{quote(offer_id, safe='')}",
            call="update_offer",
            json=payload,
            content_language=content_language,
        )
        if response.status_code not in (httpx.codes.NO_CONTENT, httpx.codes.OK):
            self._raise_for_listing(response, call="update_offer")

    async def publish_offer(self, offer_id: str) -> str:
        response = await self._request(
            "POST",
            f"/sell/inventory/v1/offer/{quote(offer_id, safe='')}/publish",
            call="publish_offer",
        )
        if response.status_code != httpx.codes.OK:
            self._raise_for_listing(response, call="publish_offer")
        listing_id = _text(response.json(), "listingId")
        if listing_id is None:
            raise EbaySellerApiUnavailableError("eBay published without returning a listing id.")
        return listing_id

    @classmethod
    def _raise_for_listing(cls, response: httpx.Response, *, call: str) -> None:
        """A 4xx on a listing call is eBay refusing *this listing* — the
        merchant needs eBay's reason (a missing item specific, a policy that
        does not fit the category), so its messages are kept. Only
        ``errors[].message`` is read; nothing else from the body."""
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            cls._raise_for(response, call=call)
        if response.is_client_error:
            messages: list[str] = []
            try:
                body = response.json()
                errors = body.get("errors") if isinstance(body, Mapping) else None
                for error in errors if isinstance(errors, list) else []:
                    if isinstance(error, Mapping) and (message := _text(error, "message")):
                        messages.append(message[:300])
            except ValueError:
                messages = []
            logger.warning("ebay_listing_rejected", call=call, status_code=response.status_code)
            raise EbayListingRejectedError(
                "eBay refused the listing: " + " ".join(messages[:3])
                if messages
                else "eBay refused the listing."
            )
        cls._raise_for(response, call=call)

    async def _get(self, path: str, *, call: str, params: Mapping[str, str] | None = None) -> Any:
        response = await self._request("GET", path, call=call, params=params)
        if response.status_code != httpx.codes.OK:
            self._raise_for(response, call=call)
        try:
            return response.json()
        except ValueError as exc:
            raise EbaySellerApiUnavailableError("eBay returned a non-JSON response.") from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        call: str,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
        content_language: str = "en-US",
    ) -> httpx.Response:
        headers = dict(self._headers)
        if json is not None:
            headers["Content-Type"] = "application/json"
            # Required by the Inventory API writes; it names the language of
            # the listing text, so it follows the marketplace.
            headers["Content-Language"] = content_language
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.request(
                    method, f"{self._base}{path}", params=params, json=json, headers=headers
                )
        except httpx.HTTPError as exc:
            logger.warning("ebay_seller_api_unreachable", call=call, error=type(exc).__name__)
            raise EbaySellerApiUnavailableError() from exc

    @staticmethod
    def _raise_for(response: httpx.Response, *, call: str) -> None:
        # Status only: eBay error bodies can echo seller account details.
        if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            logger.warning(
                "ebay_seller_api_unauthorized", call=call, status_code=response.status_code
            )
            raise EbayTokenRevokedError()
        logger.warning("ebay_seller_api_failed", call=call, status_code=response.status_code)
        raise EbaySellerApiUnavailableError()


__all__ = [
    "EBAY_MARKETPLACES",
    "EBAY_SUPPORTED_MARKETPLACES",
    "EbayBusinessPolicies",
    "EbayInventoryLocation",
    "EbayMarketplace",
    "EbayOffer",
    "EbayPolicy",
    "EbaySellerClient",
    "NewInventoryLocation",
    "parse_locations",
    "parse_opted_in",
    "parse_policies",
]
