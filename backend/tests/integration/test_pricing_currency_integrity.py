"""End-to-end proof: a real imported AliExpress product prices correctly in
the destination selling currency.

This is the test that was missing before this pass — everything else in
`test_m24a_currency_fx.py`/`test_pricing_engine.py` exercises the Money/FX/
PricingEngine pieces in isolation with mocked repositories. Nothing drove
the actual pipeline end to end: AliExpress response → mapper → stored
`ProductVariant` → `PricingEngine.draft_workspace` → the real HTTP
`GET/POST .../pricing` endpoints.

Uses the same AliExpress-mocking apparatus as `test_products.py` (a captured
real payload behind a mocked transport). The real captured fixture happens
to be USD-denominated (verified: `ae_item_base_info_dto.currency_code` and
every SKU's `currency_code` are both `"USD"`), so the CNY-specific scenarios
below use a **modified copy** of the real payload's structure with the
currency/price fields overridden to CNY — the same technique
`test_product_sync_identity.py` already uses for its add/remove/reprice
scenarios. This is not an invented shape; it is the real structure with the
specific currency this task is about substituted in, named honestly as such.

`FX_PROVIDER` is stubbed via `StubFXRateProvider`'s built-in deterministic
rates (`CNY->GBP 0.11`, `CNY->USD 0.14`) — a real Open Exchange Rates call is
neither available nor appropriate in a test.
"""

from __future__ import annotations

import copy
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.aliexpress import service as service_module
from app.models.store import Store, StorePlatform, StoreStatus
from app.services.fx.providers import StubFXRateProvider
from app.services.fx.service import FxService
from tests.integration.test_products import PRODUCT_PAYLOAD, REAL_PRODUCT_ID, connected_tenant

pytestmark = pytest.mark.integration

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
def stub_fx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic FX, no Redis cache -- the same `FxService` machinery,
    a fake rate table instead of a live Open Exchange Rates call."""
    service = FxService(StubFXRateProvider(), cache=None, allow_controlled_stale=True)
    monkeypatch.setattr("app.services.pricing_engine.get_fx_service", lambda: service)


def _cny_handler(payload: dict[str, Any]) -> Any:
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


def _cny_payload(*, sku_price: str = "40.00") -> dict[str, Any]:
    """A CNY-denominated product: the real fixture's structure, currency and
    price fields overridden — see module docstring."""
    payload = copy.deepcopy(PRODUCT_PAYLOAD)
    result = payload["aliexpress_ds_product_get_response"]["result"]
    result["ae_item_base_info_dto"]["currency_code"] = "CNY"
    for sku in result["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]:
        sku["currency_code"] = "CNY"
        sku["sku_price"] = sku_price
        sku["offer_sale_price"] = sku_price
        sku["offer_bulk_sale_price"] = sku_price
    return payload


async def _insert_verified_store(
    db_session: AsyncSession, *, tenant_id: uuid.UUID, currency: str
) -> uuid.UUID:
    """A Shopify store with a *verified* selling currency -- bypasses OAuth,
    which the pricing calculation itself does not need; only
    `currency`/`currency_last_synced_at` matter to `_resolve_selling_currency`.
    """
    from datetime import UTC, datetime

    store = Store(
        tenant_id=tenant_id,
        name=f"{currency} store",
        slug=f"store-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency=currency,
        currency_last_synced_at=datetime.now(UTC),
    )
    db_session.add(store)
    await db_session.flush()
    return store.id


async def _import_and_attach_store(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    *,
    selling_currency: str,
    sku_price: str = "40.00",
) -> tuple[dict[str, str], dict[str, Any], uuid.UUID]:
    from app.models.product import Product
    from tests.integration.test_products import patch_aliexpress

    # `connected_tenant` installs its own (USD) handler internally as part of
    # completing OAuth -- the CNY handler must be installed *after* that
    # call, or `connected_tenant` clobbers it right back to the default.
    headers = await connected_tenant(client, monkeypatch)
    patch_aliexpress(monkeypatch, _cny_handler(_cny_payload(sku_price=sku_price)))
    imported = await client.post(
        "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
    )
    assert imported.status_code == 201, imported.text
    product = imported.json()

    row = (
        await db_session.execute(select(Product).where(Product.id == uuid.UUID(product["id"])))
    ).scalar_one()
    store_id = await _insert_verified_store(
        db_session, tenant_id=row.tenant_id, currency=selling_currency
    )
    row.store_id = store_id
    await db_session.flush()

    return headers, product, store_id


class TestUkCnyToGbp:
    """Step 19 — Live Test UK, driven through a deterministic rate instead of
    a live provider call."""

    async def test_supplier_source_is_preserved_as_cny(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        response = await client.get(PRICING_URL.format(product_id=product["id"]), headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["sellingCurrency"] == "GBP"
        row = body["variants"][0]
        assert row["supplierCurrency"] == "CNY"
        assert Decimal(row["supplierCost"]) == Decimal("40.00")

    async def test_converted_cost_is_gbp_using_the_deterministic_rate(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        response = await client.get(PRICING_URL.format(product_id=product["id"]), headers=headers)
        body = response.json()
        row = body["variants"][0]

        # StubFXRateProvider: CNY -> GBP = 0.11
        assert row["convertedCurrency"] == "GBP"
        assert Decimal(row["convertedCost"]) == (Decimal("40.00") * Decimal("0.11")).quantize(
            Decimal("0.0001")
        )
        assert body["fxRate"] == "0.1100"
        assert body["fxBaseCurrency"] == "CNY"
        assert body["fxQuoteCurrency"] == "GBP"
        assert body["fxProvider"] == "stub"

    async def test_fifty_percent_markup_applies_after_conversion_not_before(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """The exact bug this whole task is about: markup must multiply the
        *converted* GBP cost, never the raw CNY number relabelled."""
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        response = await client.post(
            PRICING_PREVIEW_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        row = response.json()["variants"][0]

        converted = Decimal(row["convertedCost"])  # ~4.4000 GBP
        proposed = Decimal(row["proposedSellPrice"])
        # proposed = converted * 1.5 -- NOT 40.00 * 1.5 = 60.00.
        assert proposed == (converted * Decimal("1.5")).quantize(Decimal("0.01"))
        assert proposed != Decimal("60.00")
        assert converted == (Decimal("40.00") * Decimal("0.11")).quantize(Decimal("0.0001"))

    async def test_apply_persists_gbp_price_and_currency(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        applied = await client.post(
            PRICING_APPLY_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        row = applied.json()["variants"][0]
        # `apply`'s own response reloads the workspace without re-proposing
        # (that is `preview`'s job), so `proposedSellPrice` is legitimately
        # null here -- `sellPrice`, the value actually written, is what
        # matters for this test.
        assert row["sellPrice"] is not None
        converted = (Decimal("40.00") * Decimal("0.11")).quantize(Decimal("0.0001"))
        assert Decimal(row["sellPrice"]) == (converted * Decimal("1.5")).quantize(Decimal("0.01"))

        # Reload from scratch -- persisted, not just echoed in the response.
        reloaded = await client.get(PRICING_URL.format(product_id=product["id"]), headers=headers)
        reloaded_row = reloaded.json()["variants"][0]
        assert Decimal(reloaded_row["sellPrice"]) == Decimal(row["sellPrice"])
        assert reloaded_row["needsRecalculation"] is False

    async def test_no_cny_anywhere_in_sell_proposed_profit_break_even(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        response = await client.post(
            PRICING_PREVIEW_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        body = response.json()
        assert body["sellingCurrency"] == "GBP"
        assert body["currency"] == "GBP"
        # The only place CNY may legitimately appear is the supplier column.
        for row in body["variants"]:
            assert row["supplierCurrency"] == "CNY"
            assert row["convertedCurrency"] == "GBP"


class TestUsaCnyToUsd:
    """Step 20 — Live Test USA."""

    async def test_full_pipeline_uses_usd_throughout(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="USD"
        )
        response = await client.post(
            PRICING_PREVIEW_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        row = body["variants"][0]

        assert body["sellingCurrency"] == "USD"
        assert row["supplierCurrency"] == "CNY"
        assert row["convertedCurrency"] == "USD"
        # StubFXRateProvider: CNY -> USD = 0.14
        converted = (Decimal("40.00") * Decimal("0.14")).quantize(Decimal("0.0001"))
        assert Decimal(row["convertedCost"]) == converted
        assert Decimal(row["proposedSellPrice"]) == (converted * Decimal("1.5")).quantize(
            Decimal("0.01")
        )
        assert Decimal(row["profit"]) == Decimal(row["proposedSellPrice"]) - converted
        assert Decimal(row["breakEvenPrice"]) == converted


class TestFailClosed:
    async def test_an_unsynced_shopify_store_blocks_pricing_never_falls_back(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """A Shopify store is attached, but its currency has never been
        verified (`currency_last_synced_at` is null) -- must block, and must
        specifically **not** fall back to the tenant's USD default, the
        supplier's CNY, or any other guess. This is the scenario every real
        newly-connected Shopify store starts in before its first currency
        sync, so it is the realistic failure mode, not a contrived one.
        """
        from app.models.product import Product
        from tests.integration.test_products import patch_aliexpress

        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, _cny_handler(_cny_payload()))
        imported = await client.post(
            "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
        )
        product = imported.json()
        row = (
            await db_session.execute(select(Product).where(Product.id == uuid.UUID(product["id"])))
        ).scalar_one()

        unsynced_store = Store(
            tenant_id=row.tenant_id,
            name="Unsynced store",
            slug=f"store-{uuid.uuid4().hex[:8]}",
            platform=StorePlatform.SHOPIFY,
            status=StoreStatus.CONNECTED,
            currency="USD",  # present, but...
            currency_last_synced_at=None,  # ...never verified.
        )
        db_session.add(unsynced_store)
        await db_session.flush()
        row.store_id = unsynced_store.id
        await db_session.flush()

        response = await client.get(PRICING_URL.format(product_id=product["id"]), headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["pricingBlocked"] is True
        assert body["pricingBlockCode"] == "selling_currency_missing"
        assert body["sellingCurrency"] is None
        assert body["variants"] == []
        assert body["fxNote"]

    async def test_apply_is_rejected_when_selling_currency_is_unresolved(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        from app.models.product import Product
        from tests.integration.test_products import patch_aliexpress

        headers = await connected_tenant(client, monkeypatch)
        patch_aliexpress(monkeypatch, _cny_handler(_cny_payload()))
        imported = await client.post(
            "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
        )
        product = imported.json()
        row = (
            await db_session.execute(select(Product).where(Product.id == uuid.UUID(product["id"])))
        ).scalar_one()
        unsynced_store = Store(
            tenant_id=row.tenant_id,
            name="Unsynced store",
            slug=f"store-{uuid.uuid4().hex[:8]}",
            platform=StorePlatform.SHOPIFY,
            status=StoreStatus.CONNECTED,
            currency="USD",
            currency_last_synced_at=None,
        )
        db_session.add(unsynced_store)
        await db_session.flush()
        row.store_id = unsynced_store.id
        await db_session.flush()

        response = await client.post(
            PRICING_APPLY_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        assert response.status_code in (409, 422, 400), response.text
        # Never a 200 with invented numbers.
        if response.status_code == 200:  # pragma: no cover -- defence in depth
            pytest.fail("apply must not succeed while pricing is blocked")

    async def test_fx_unavailable_blocks_the_row_without_inventing_a_price(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
    ) -> None:
        """A currency pair the stub table has no rate for at all (and no
        inverse to derive one from) -- `FxService` must return `None`, and
        the row must block rather than silently pass the raw number through.

        `StubFXRateProvider`'s built-in defaults (CNY/USD/GBP/EUR) are always
        merged in via `setdefault`, even when constructed with `rates={}` --
        an *empty* table isn't achievable that way. JPY is absent from every
        default pair and has no derivable inverse, so CNY -> JPY is
        genuinely unavailable without needing to fight that behaviour.
        """
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="JPY"
        )
        response = await client.get(PRICING_URL.format(product_id=product["id"]), headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["pricingBlocked"] is True
        row = body["variants"][0]
        assert row["rowBlocked"] is True
        assert row["convertedCost"] is None
        assert row["proposedSellPrice"] is None
        assert row["profit"] is None


class TestNeedsRecalculation:
    """Step 16 — a price computed under one selling currency must be flagged,
    never silently relabelled, once the resolved selling currency changes."""

    async def test_a_freshly_applied_price_does_not_need_recalculation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        headers, product, _store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        applied = await client.post(
            PRICING_APPLY_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["needsRecalculation"] is False
        assert all(not row["needsRecalculation"] for row in applied.json()["variants"])

    async def test_switching_the_destination_store_currency_flags_recalculation(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        """Price under GBP, then move the product to a USD store. The old
        GBP-denominated `sellPrice` is still sitting in the column -- it must
        be flagged, not silently displayed as if it were USD."""
        from app.models.product import Product

        headers, product, _gbp_store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        applied = await client.post(
            PRICING_APPLY_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        assert applied.status_code == 200, applied.text
        gbp_sell_price = applied.json()["variants"][0]["sellPrice"]

        row = (
            await db_session.execute(select(Product).where(Product.id == uuid.UUID(product["id"])))
        ).scalar_one()
        usd_store_id = await _insert_verified_store(
            db_session, tenant_id=row.tenant_id, currency="USD"
        )
        row.store_id = usd_store_id
        await db_session.flush()

        response = await client.get(PRICING_URL.format(product_id=product["id"]), headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["sellingCurrency"] == "USD"
        assert body["needsRecalculation"] is True
        variant_row = body["variants"][0]
        assert variant_row["needsRecalculation"] is True
        # The stale number is still visible (not hidden), just flagged --
        # the merchant sees exactly what is stored, honestly labelled.
        # Compared as Decimal, not string: a value round-tripped through
        # Postgres can render with different trailing zeros than the
        # freshly-computed one did ("6.60" vs "6.6000") -- a serialization
        # detail, not a sign the price actually changed.
        assert Decimal(variant_row["sellPrice"]) == Decimal(gbp_sell_price)

    async def test_recalculating_for_the_new_currency_clears_the_flag(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        from app.models.product import Product

        headers, product, _gbp_store_id = await _import_and_attach_store(
            client, monkeypatch, db_session, selling_currency="GBP"
        )
        await client.post(
            PRICING_APPLY_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        row = (
            await db_session.execute(select(Product).where(Product.id == uuid.UUID(product["id"])))
        ).scalar_one()
        usd_store_id = await _insert_verified_store(
            db_session, tenant_id=row.tenant_id, currency="USD"
        )
        row.store_id = usd_store_id
        await db_session.flush()

        reapplied = await client.post(
            PRICING_APPLY_URL.format(product_id=product["id"]),
            json={"mode": "percentage_markup", "markupPercent": "50"},
            headers=headers,
        )
        assert reapplied.status_code == 200, reapplied.text
        body = reapplied.json()
        assert body["needsRecalculation"] is False
        assert body["variants"][0]["needsRecalculation"] is False
        assert body["variants"][0]["sellPrice"] is not None
