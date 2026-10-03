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

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ebay.exceptions import (
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

_BUSINESS_POLICIES_PROGRAM = "SELLING_POLICY_MANAGEMENT"
#: eBay's page-size ceiling for getInventoryLocations.
_LOCATION_PAGE_LIMIT = 100


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
    ) -> httpx.Response:
        headers = dict(self._headers)
        if json is not None:
            headers["Content-Type"] = "application/json"
            # Required by createInventoryLocation; harmless elsewhere.
            headers["Content-Language"] = "en-US"
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
    "EBAY_SUPPORTED_MARKETPLACES",
    "EbayBusinessPolicies",
    "EbayInventoryLocation",
    "EbayPolicy",
    "EbaySellerClient",
    "NewInventoryLocation",
    "parse_locations",
    "parse_opted_in",
    "parse_policies",
]
