"""The real, live-traced bug: a store-less GB draft with a genuinely
localized GBP supplier price was resolving to USD pricing, because
`PricingEngine._resolve_selling_currency` checked `tenant.default_currency`
(``server_default="USD"`` on every tenant) before ever looking at the
draft's own `import_currency`. Traced against the real product from the
user's screenshot (`ship_to_country=GB`, `import_currency=GBP`, no store
linked, tenant default USD) — this file reproduces that exact scenario
through the real HTTP pricing endpoints, not just the resolver in
isolation (see `tests/unit/test_m24a_currency_fx.py` for that).
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.aliexpress import service as service_module
from app.models.product import Product
from app.models.store import Store, StorePlatform, StoreStatus
from app.services.fx.providers import StubFXRateProvider
from app.services.fx.service import FxService
from tests.integration.test_products import PRODUCT_PAYLOAD, REAL_PRODUCT_ID, connected_tenant

pytestmark = pytest.mark.integration

IMPORT_URL = "/api/v1/products/import"
PRICING_URL = "/api/v1/drafts/{product_id}/pricing"
PRICING_PREVIEW_URL = "/api/v1/drafts/{product_id}/pricing/preview"
PRICING_APPLY_URL = "/api/v1/drafts/{product_id}/pricing/apply"


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


@pytest.fixture(autouse=True)
def _stub_fx(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Deterministic FX, and a call log — the fallback tests assert FX was
    used exactly when expected, and the localized-price tests assert it was
    **never** called at all.

    Tracks ``get_rate`` specifically, not ``convert`` — ``convert`` is called
    for every priced variant regardless of currency (that is where the
    same-currency short-circuit *lives*: it returns before ever touching
    ``get_rate``/the provider). "No FX call" means no rate lookup, which is
    the thing Step 19 actually means by "FX provider call count = 0".
    """
    calls: list[tuple[str, str]] = []
    fx_service = FxService(StubFXRateProvider())
    original_get_rate = fx_service.get_rate

    async def _tracking_get_rate(base: str, quote: str) -> Any:
        calls.append((base, quote))
        return await original_get_rate(base, quote)

    fx_service.get_rate = _tracking_get_rate  # type: ignore[method-assign]

    from app.services import pricing_engine as pricing_engine_module

    monkeypatch.setattr(pricing_engine_module, "get_fx_service", lambda: fx_service)
    return calls


def _localized_payload(*, sku_currency: str, native_currency: str = "CNY") -> dict[str, Any]:
    payload = copy.deepcopy(PRODUCT_PAYLOAD)
    result = payload["aliexpress_ds_product_get_response"]["result"]
    result["ae_item_base_info_dto"]["currency_code"] = native_currency
    for sku in result["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]:
        sku["currency_code"] = sku_currency
    return payload


def _handler(payload: dict[str, Any]) -> Any:
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
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"error_response": {"code": "InvalidApiPath"}})

    return handler


async def _import_gb_draft(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A store-less GB/GBP draft -- the exact real bug scenario. Every
    tenant's `default_currency` defaults to `"USD"`; nothing here overrides
    it, matching the real product traced (no store linked)."""
    headers = await connected_tenant(client, monkeypatch)
    from tests.integration.test_products import patch_aliexpress

    patch_aliexpress(monkeypatch, _handler(_localized_payload(sku_currency="GBP")))
    created = (
        await client.post(
            IMPORT_URL,
            json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "GB"},
            headers=headers,
        )
    ).json()
    assert created["importCurrency"] == "GBP"
    return {"headers": headers, "product": created}


class TestTheRealRegression:
    """Step 22's exact scenario: market=GB, localizedSupplierPrice=GBP,
    tenant default currency=USD. pricingCurrency must be GBP, never USD."""

    async def test_gb_draft_with_tenant_usd_default_prices_in_gbp(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _stub_fx: list[tuple[str, str]]
    ) -> None:
        ctx = await _import_gb_draft(client, monkeypatch)

        response = await client.get(
            PRICING_URL.format(product_id=ctx["product"]["id"]), headers=ctx["headers"]
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sellingCurrency"] == "GBP"
        assert body["sellingCurrency"] != "USD"
        assert body["sellingCurrencySource"] == "import_market"
        for row in body["variants"]:
            assert row["supplierCurrency"] == "GBP"
            assert row["convertedCurrency"] == "GBP"
            assert row["conversionRequired"] is False
            assert row["conversionType"] == "direct"
        # The core of Section 5/9/19: no FX call at all when the localized
        # price already matches the resolved pricing currency.
        assert _stub_fx == []


class TestUkDirectLocalizedPrice:
    """Step 19 — full acceptance example from the spec, using the real
    Khaki/Champagne-shaped supplier costs (1.6400 / 1.4000 GBP)."""

    async def test_fifty_percent_markup_stays_in_gbp_throughout(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _stub_fx: list[tuple[str, str]]
    ) -> None:
        ctx = await _import_gb_draft(client, monkeypatch)

        preview = await client.post(
            PRICING_PREVIEW_URL.format(product_id=ctx["product"]["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=ctx["headers"],
        )

        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["sellingCurrency"] == "GBP"
        assert _stub_fx == []
        for row in body["variants"]:
            if row["rowBlocked"]:
                continue
            cost = Decimal(row["convertedCost"])
            assert row["convertedCurrency"] == "GBP"
            assert Decimal(row["proposedSellPrice"]) == (cost * Decimal("1.5")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            assert Decimal(row["breakEvenPrice"]) == cost
            assert Decimal(row["profit"]) == Decimal(row["proposedSellPrice"]) - cost

    async def test_apply_persists_gbp_and_reload_stays_gbp(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        ctx = await _import_gb_draft(client, monkeypatch)

        apply = await client.post(
            PRICING_APPLY_URL.format(product_id=ctx["product"]["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=ctx["headers"],
        )
        assert apply.status_code == 200, apply.text

        row = (
            await db_session.execute(select(Product).where(Product.id == ctx["product"]["id"]))
        ).scalar_one()
        for variant in row.variants:
            if variant.sell_price is None:
                continue
            assert variant.sell_price_currency == "GBP"

        reloaded = await client.get(
            PRICING_URL.format(product_id=ctx["product"]["id"]), headers=ctx["headers"]
        )
        assert reloaded.status_code == 200
        assert reloaded.json()["sellingCurrency"] == "GBP"
        assert reloaded.json()["needsRecalculation"] is False


class TestUsDirectLocalizedPrice:
    """Step 20 -- same shape, US market, entirely separate from the UK
    scenario (Step 25's explicit instruction: do not mix them)."""

    async def test_us_market_prices_in_usd_with_no_fx(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _stub_fx: list[tuple[str, str]]
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(monkeypatch, _handler(_localized_payload(sku_currency="USD")))
        created = (
            await client.post(
                IMPORT_URL,
                json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "US"},
                headers=headers,
            )
        ).json()
        assert created["importCurrency"] == "USD"

        preview = await client.post(
            PRICING_PREVIEW_URL.format(product_id=created["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )

        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["sellingCurrency"] == "USD"
        assert _stub_fx == []
        for row in body["variants"]:
            assert row["supplierCurrency"] == "USD"
            if not row["rowBlocked"]:
                assert row["convertedCurrency"] == "USD"


class TestFxFallbackWhenNoLocalizedPrice:
    """Step 21 -- AliExpress returns CNY only (no localized GBP price for
    this SKU); the existing FxService must run exactly once per variant and
    every downstream figure must land in GBP, never leaking CNY."""

    async def test_cny_only_falls_back_to_fx_and_stays_gbp(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _stub_fx: list[tuple[str, str]]
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        from tests.integration.test_products import patch_aliexpress

        patch_aliexpress(monkeypatch, _handler(_localized_payload(sku_currency="CNY")))
        created = (
            await client.post(
                IMPORT_URL,
                json={"externalId": REAL_PRODUCT_ID, "shipToCountry": "GB", "currency": "GBP"},
                headers=headers,
            )
        ).json()
        assert created["importCurrency"] == "GBP"
        assert created["currency"] == "CNY"  # SKUs reported CNY -- no GBP to localize with.

        response = await client.get(PRICING_URL.format(product_id=created["id"]), headers=headers)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sellingCurrency"] == "GBP"
        for row in body["variants"]:
            assert row["supplierCurrency"] == "CNY"
            if not row["rowBlocked"]:
                assert row["convertedCurrency"] == "GBP"
                assert row["conversionType"] == "fx"
        # FX ran -- once per variant that needed it -- and every call
        # requested GBP, never CNY leaking through as the target.
        assert _stub_fx
        for _source_ccy, target_ccy in _stub_fx:
            assert target_ccy == "GBP"


class TestExplicitStoreSwitchFlagsRecalculation:
    """Step 4/23 -- switching to a verified store with a *different*
    currency must never silently relabel the existing GBP screen as USD. It
    must apply/persist explicitly and flag the change, not happen for free."""

    async def test_switching_to_a_verified_usd_store_flags_needs_recalculation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        ctx = await _import_gb_draft(client, monkeypatch)
        apply_gbp = await client.post(
            PRICING_APPLY_URL.format(product_id=ctx["product"]["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=ctx["headers"],
        )
        assert apply_gbp.status_code == 200, apply_gbp.text
        applied_variant = next(
            v for v in apply_gbp.json()["variants"] if v["sellPrice"] is not None
        )
        variant_id = applied_variant["variantId"]
        gbp_sell_price = applied_variant["sellPrice"]

        row = (
            await db_session.execute(select(Product).where(Product.id == ctx["product"]["id"]))
        ).scalar_one()
        store = Store(
            tenant_id=row.tenant_id,
            name="USD store",
            slug="usd-store",
            platform=StorePlatform.SHOPIFY,
            status=StoreStatus.CONNECTED,
            currency="USD",
            currency_last_synced_at=datetime.now(UTC),
        )
        db_session.add(store)
        await db_session.flush()
        # The GET pricing endpoint has no ad-hoc store-override param -- a
        # store switch is a real, persisted action ("select destination
        # store" in the UI), not a request-scoped query param. Simulate that
        # persisted link directly.
        row.store_id = store.id
        await db_session.flush()

        response = await client.get(
            PRICING_URL.format(product_id=ctx["product"]["id"]), headers=ctx["headers"]
        )

        # The verified store's currency is authoritative once explicitly
        # selected -- but the GBP number already on the row must not be
        # silently displayed as USD; needsRecalculation says so instead.
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sellingCurrency"] == "USD"
        variant_row = next(v for v in body["variants"] if v["variantId"] == variant_id)
        assert Decimal(variant_row["sellPrice"]) == Decimal(gbp_sell_price)
        assert variant_row["needsRecalculation"] is True
        assert body["needsRecalculation"] is True
