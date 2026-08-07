"""M24B — AliExpress `target_currency` is derived from destination, not hardcoded.

The request shape (`target_currency`/`target_language`/`ship_to_country`) was
already correct before this pass — `ProductImportService._fetch_product`
already sent all three. The actual, live-traced bug was upstream: every
refresh/sync call site omitted `currency` entirely, which silently defaulted
to `"USD"` regardless of the product's real destination. A GB-destined
product would ask AliExpress for USD pricing on every refresh.

These tests assert on the *outgoing request*, not just the stored result —
the bug was specifically that the wrong thing got requested, not that the
response was mis-parsed.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.aliexpress import service as service_module
from app.models.product import Product
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.test_products import PRODUCT_PAYLOAD, REAL_PRODUCT_ID, connected_tenant

pytestmark = pytest.mark.integration

IMPORT_URL = "/api/v1/products/import"
SYNC_URL = "/api/v1/products/{product_id}/sync"
DRAFT_REFRESH_URL = "/api/v1/drafts/{product_id}/refresh"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


def _localized_payload(*, sku_currency: str, native_currency: str = "CNY") -> dict[str, Any]:
    """A copy of the real captured fixture with currencies overridden.

    Live-traced shape, not invented: a real GB/GBP request for a real
    product returned exactly this split — `ae_item_base_info_dto.currency_code`
    stays the seller's native currency regardless of what was requested,
    while every SKU's own `currency_code` reports the requested target.
    """
    payload = copy.deepcopy(PRODUCT_PAYLOAD)
    result = payload["aliexpress_ds_product_get_response"]["result"]
    result["ae_item_base_info_dto"]["currency_code"] = native_currency
    for sku in result["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]:
        sku["currency_code"] = sku_currency
    return payload


def _recording_handler(payload: dict[str, Any], captured_requests: list[dict[str, str]]) -> Any:
    """Answers OAuth + product.get, and records every product.get request's
    form-encoded params so a test can assert what was actually sent, not
    just what came back."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "/auth/token" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "access_token": "issued-access-token",
                    "refresh_token": "issued-refresh-token",
                    "expires_in": 86400,
                    "user_id": "seller-1",
                },
            )
        if "aliexpress.ds.product.get" in body:
            parsed = {k: v[0] for k, v in parse_qs(body).items()}
            captured_requests.append(parsed)
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"error_response": {"code": "InvalidApiPath"}})

    return handler


async def _insert_verified_store(
    db_session: AsyncSession, *, tenant_id: uuid.UUID, currency: str, synced: bool
) -> uuid.UUID:
    store = Store(
        tenant_id=tenant_id,
        name=f"{currency} store",
        slug=f"store-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency=currency,
        currency_last_synced_at=datetime.now(UTC) if synced else None,
    )
    db_session.add(store)
    await db_session.flush()
    return store.id


class TestCurrencyDerivedFromDestination:
    async def test_gb_destination_requests_gbp_with_no_currency_given(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `connected_tenant` installs its own (USD-fixture) handler internally
        # as part of completing OAuth -- ours must be installed after, or it
        # gets clobbered right back.
        headers = await connected_tenant(client, monkeypatch)
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="GBP"), captured)
        )

        response = await client.post(
            IMPORT_URL,
            json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "GB"},
            headers=headers,
        )

        assert response.status_code == 201, response.text
        assert captured, "product.get was never called"
        assert captured[-1]["ship_to_country"] == "GB"
        assert captured[-1]["target_currency"] == "GBP"

    async def test_us_destination_requests_usd_with_no_currency_given(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="USD"), captured)
        )

        response = await client.post(
            IMPORT_URL,
            json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "US"},
            headers=headers,
        )

        assert response.status_code == 201, response.text
        assert captured[-1]["ship_to_country"] == "US"
        assert captured[-1]["target_currency"] == "USD"

    async def test_unmapped_destination_falls_back_to_workspace_default(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DE is not GB or US -- the initial market map -- so this must fall
        through to the tenant's default_currency (USD) rather than error or
        invent a currency for an unapproved market."""
        headers = await connected_tenant(client, monkeypatch)
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="EUR"), captured)
        )

        response = await client.post(
            IMPORT_URL,
            json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "DE"},
            headers=headers,
        )

        assert response.status_code == 201, response.text
        assert captured[-1]["ship_to_country"] == "DE"
        assert captured[-1]["target_currency"] == "USD"


class TestExplicitCurrencyOverride:
    async def test_explicit_currency_wins_over_destination_mapping(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="EUR"), captured)
        )

        response = await client.post(
            IMPORT_URL,
            json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "GB", "currency": "EUR"},
            headers=headers,
        )

        assert response.status_code == 201, response.text
        # GB alone would map to GBP -- the explicit override must win.
        assert captured[-1]["target_currency"] == "EUR"


async def _import_once_to_learn_tenant_id(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    headers: dict[str, str],
) -> uuid.UUID:
    """A throwaway first import purely to discover this tenant's id — the
    same technique `test_pricing_currency_integrity.py` uses, since nothing
    here needs a dedicated "who am I" endpoint."""
    from tests.integration.test_products import patch_aliexpress

    patch_aliexpress(monkeypatch, _recording_handler(_localized_payload(sku_currency="USD"), []))
    created = (
        await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
    ).json()
    row = (
        await db_session.execute(select(Product).where(Product.id == uuid.UUID(created["id"])))
    ).scalar_one()
    return row.tenant_id


class TestVerifiedStoreOverridesDestination:
    async def test_a_verified_stores_currency_wins_over_the_gb_map(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """Section 4's own example: workspace/destination says GBP, but the
        selected Shopify store actually sells in USD -- USD must win for that
        publication."""
        headers = await connected_tenant(client, monkeypatch)
        tenant_id = await _import_once_to_learn_tenant_id(client, monkeypatch, db_session, headers)
        store_id = await _insert_verified_store(
            db_session, tenant_id=tenant_id, currency="USD", synced=True
        )
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="USD"), captured)
        )

        # Re-import (idempotent -- same external id) now with the store set.
        response = await client.post(
            IMPORT_URL,
            json={
                "externalId": REAL_PRODUCT_ID,
                "shipToCountry": "GB",
                "storeId": str(store_id),
            },
            headers=headers,
        )

        assert response.status_code == 201, response.text
        assert captured[-1]["ship_to_country"] == "GB"
        assert captured[-1]["target_currency"] == "USD"

    async def test_an_unverified_store_does_not_override_destination(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """An unsynced store's `currency` is untrusted -- same M24A authority
        rule `PricingEngine._resolve_selling_currency` already enforces.
        Falls through to the GB->GBP destination map, not the store's
        unverified USD."""
        headers = await connected_tenant(client, monkeypatch)
        tenant_id = await _import_once_to_learn_tenant_id(client, monkeypatch, db_session, headers)
        store_id = await _insert_verified_store(
            db_session, tenant_id=tenant_id, currency="USD", synced=False
        )
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="GBP"), captured)
        )

        response = await client.post(
            IMPORT_URL,
            json={
                "externalId": REAL_PRODUCT_ID,
                "shipToCountry": "GB",
                "storeId": str(store_id),
            },
            headers=headers,
        )

        assert response.status_code == 201, response.text
        assert captured[-1]["target_currency"] == "GBP"


class TestRefreshDoesNotRevertToUsd:
    """The actual regression: every refresh/sync path used to omit `currency`
    and silently default to `"USD"` regardless of destination."""

    async def test_products_sync_reuses_the_original_currency(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="GBP"), captured)
        )
        created = (
            await client.post(
                IMPORT_URL,
                json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "GB"},
                headers=headers,
            )
        ).json()
        assert captured[-1]["target_currency"] == "GBP"

        response = await client.post(SYNC_URL.format(product_id=created["id"]), headers=headers)

        assert response.status_code == 200, response.text
        assert captured[-1]["ship_to_country"] == "GB"
        # The bug: this used to be "USD" because `sync_product` never passed
        # `currency` and the old hardcoded default filled the gap.
        assert captured[-1]["target_currency"] == "GBP"

    async def test_draft_refresh_reuses_the_original_currency(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        captured: list[dict[str, str]] = []
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch, _recording_handler(_localized_payload(sku_currency="GBP"), captured)
        )
        created = (
            await client.post(
                IMPORT_URL,
                json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "GB"},
                headers=headers,
            )
        ).json()

        response = await client.post(
            DRAFT_REFRESH_URL.format(product_id=created["id"]), headers=headers
        )

        assert response.status_code == 200, response.text
        assert captured[-1]["ship_to_country"] == "GB"
        assert captured[-1]["target_currency"] == "GBP"


class TestSupplierCurrencyFieldsPreserved:
    async def test_product_currency_is_sku_derived_not_the_unlocalized_base(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """The real, live-reproduced bug: `ae_item_base_info_dto.currency_code`
        stays CNY regardless of the requested target_currency, but
        `cost_price_min`/`cost_price_max` are computed from the (correctly
        localized) SKU prices. Pairing them with the unlocalized base
        currency mislabels the number. `Product.currency` must match what
        `cost_price_min`/`cost_price_max` are actually in;
        `supplier_native_currency` keeps the base/native currency separately,
        for audit only."""
        headers = await connected_tenant(client, monkeypatch)
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(
            monkeypatch,
            _recording_handler(_localized_payload(sku_currency="GBP", native_currency="CNY"), []),
        )

        created = (
            await client.post(
                IMPORT_URL,
                json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "GB"},
                headers=headers,
            )
        ).json()

        assert created["currency"] == "GBP"
        assert created["supplierNativeCurrency"] == "CNY"
        assert created["importCurrency"] == "GBP"

        # Persisted, not just in the response.
        row = (
            await db_session.execute(select(Product).where(Product.id == uuid.UUID(created["id"])))
        ).scalar_one()
        assert row.currency == "GBP"
        assert row.supplier_native_currency == "CNY"
        assert row.import_currency == "GBP"
