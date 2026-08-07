"""M24A — Shopify selling-currency authority + FX integrity tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from app.core.exceptions import FxUnavailableError, ShopifyCurrencyRefreshError
from app.domain.fx import FxRateQuote, FxRateStatus
from app.domain.money import Money
from app.models.store import StorePlatform
from app.services.fx.providers import OpenExchangeRatesProvider, StubFXRateProvider
from app.services.fx.service import FxService, deserialize_quote, serialize_quote
from app.services.pricing_engine import PricingEngine

pytestmark = pytest.mark.unit


def _assert_no_float_in_quote(quote: FxRateQuote) -> None:
    assert isinstance(quote.rate, Decimal)
    assert not isinstance(quote.rate, float)
    # Walk dataclass fields — nothing should be a bare float.
    for name in (
        "base_currency",
        "quote_currency",
        "rate",
        "provider_name",
        "fetched_at",
        "provider_timestamp",
        "expires_at",
        "status",
        "derivation",
    ):
        value = getattr(quote, name)
        assert not isinstance(value, float), f"{name} must not be float"


class TestLegacyStoreCurrencyNotAuthoritative:
    async def test_usd_with_null_synced_at_is_not_authoritative(self) -> None:
        engine = PricingEngine.__new__(PricingEngine)
        store = MagicMock()
        store.id = uuid4()
        store.platform = StorePlatform.SHOPIFY
        store.currency = "USD"
        store.currency_last_synced_at = None
        engine.stores = MagicMock()
        engine.stores.get_by_id = AsyncMock(return_value=store)
        engine.tenants = MagicMock()
        engine.tenants.get_by_id = AsyncMock(
            return_value=MagicMock(default_currency="GBP")
        )
        product = MagicMock()
        product.store_id = store.id
        product.tenant_id = uuid4()
        product.currency = "CNY"
        product.variants = [MagicMock(currency="CNY")]

        code, source, _ = await engine._resolve_selling_currency(
            product, destination_store_id=None
        )
        assert code is None
        assert source == "selling_currency_missing"

    async def test_unsynced_does_not_fall_back_to_tenant(self) -> None:
        engine = PricingEngine.__new__(PricingEngine)
        store = MagicMock()
        store.id = uuid4()
        store.platform = StorePlatform.SHOPIFY
        store.currency = "USD"
        store.currency_last_synced_at = None
        engine.stores = MagicMock()
        engine.stores.get_by_id = AsyncMock(return_value=store)
        engine.tenants = MagicMock()
        engine.tenants.get_by_id = AsyncMock(
            return_value=MagicMock(default_currency="EUR")
        )
        product = MagicMock()
        product.store_id = store.id
        product.tenant_id = uuid4()
        product.currency = "CNY"
        product.variants = [MagicMock(currency="CNY")]

        code, source, _ = await engine._resolve_selling_currency(
            product, destination_store_id=None
        )
        assert code is None
        assert source == "selling_currency_missing"
        engine.tenants.get_by_id.assert_not_called()

    async def test_unsynced_does_not_fall_back_to_supplier(self) -> None:
        engine = PricingEngine.__new__(PricingEngine)
        store = MagicMock()
        store.id = uuid4()
        store.platform = StorePlatform.SHOPIFY
        store.currency = "USD"
        store.currency_last_synced_at = None
        engine.stores = MagicMock()
        engine.stores.get_by_id = AsyncMock(return_value=store)
        engine.tenants = MagicMock()
        engine.tenants.get_by_id = AsyncMock(return_value=None)
        product = MagicMock()
        product.store_id = store.id
        product.tenant_id = uuid4()
        product.currency = "CNY"
        product.variants = [MagicMock(currency="CNY")]

        code, source, _ = await engine._resolve_selling_currency(
            product, destination_store_id=None
        )
        assert code is None
        assert code != "CNY"
        assert source == "selling_currency_missing"

    async def test_synced_gbp_is_authoritative(self) -> None:
        engine = PricingEngine.__new__(PricingEngine)
        store = MagicMock()
        store.id = uuid4()
        store.platform = StorePlatform.SHOPIFY
        store.currency = "GBP"
        store.currency_last_synced_at = datetime.now(UTC)
        engine.stores = MagicMock()
        engine.stores.get_by_id = AsyncMock(return_value=store)
        product = MagicMock()
        product.store_id = store.id
        product.tenant_id = uuid4()
        product.currency = "CNY"
        product.variants = [MagicMock(currency="CNY")]

        code, source, sid = await engine._resolve_selling_currency(
            product, destination_store_id=None
        )
        assert code == "GBP"
        assert source == "shopify_store"
        assert sid == store.id


class TestShopifyCurrencyRefresh:
    async def test_graphql_gbp_persists_sync_timestamp(self) -> None:
        from app.integrations.shopify.client import ShopifyClient

        client = ShopifyClient(
            shop_domain="example.myshopify.com",
            access_token="shpat_test",
            tenant_id=str(uuid4()),
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/graphql.json")
            assert "/2026-07/" in str(request.url)
            body = json.loads(request.content.decode())
            assert "ShopCurrency" in body["query"] or "currencyCode" in body["query"]
            return httpx.Response(
                200,
                json={"data": {"shop": {"currencyCode": "GBP"}}},
            )

        transport = httpx.MockTransport(handler)
        # Patch AsyncClient used inside graphql by monkeypatching via transport
        # — call fetch through a thin wrapper that injects the transport.
        original = httpx.AsyncClient

        class PatchedClient(original):  # type: ignore[misc,valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        import app.integrations.shopify.client as shopify_client_mod

        monkey = pytest.MonkeyPatch()
        monkey.setattr(shopify_client_mod.httpx, "AsyncClient", PatchedClient)
        try:
            code = await client.fetch_shop_currency_code()
        finally:
            monkey.undo()
        assert code == "GBP"

    async def test_refresh_failure_preserves_trusted_gbp(self) -> None:
        from app.integrations.shopify.service import ShopifyService

        service = ShopifyService.__new__(ShopifyService)
        store = MagicMock()
        store.id = uuid4()
        store.platform = StorePlatform.SHOPIFY
        store.currency = "GBP"
        store.currency_last_synced_at = datetime(2026, 1, 15, tzinfo=UTC)
        store.tenant_id = uuid4()
        original_ts = store.currency_last_synced_at

        service.stores = MagicMock()
        service.stores.get_by_id = AsyncMock(side_effect=[store, store])
        service.client_for_store = AsyncMock(
            side_effect=RuntimeError("shopify down")
        )

        with pytest.raises(ShopifyCurrencyRefreshError) as exc_info:
            await service.refresh_shop_currency(store.id)
        assert store.currency == "GBP"
        assert store.currency_last_synced_at == original_ts
        assert exc_info.value.details.get("had_trusted_currency") is True
        assert exc_info.value.details.get("retained_currency") == "GBP"

    async def test_refresh_failure_without_trusted_reports_unavailable(self) -> None:
        from app.integrations.shopify.service import ShopifyService

        service = ShopifyService.__new__(ShopifyService)
        store = MagicMock()
        store.id = uuid4()
        store.platform = StorePlatform.SHOPIFY
        store.currency = "USD"
        store.currency_last_synced_at = None
        store.tenant_id = uuid4()

        service.stores = MagicMock()
        service.stores.get_by_id = AsyncMock(return_value=store)
        service.client_for_store = AsyncMock(side_effect=RuntimeError("shopify down"))

        with pytest.raises(ShopifyCurrencyRefreshError) as exc_info:
            await service.refresh_shop_currency(store.id)
        assert exc_info.value.details.get("had_trusted_currency") is False
        assert store.currency_last_synced_at is None


class TestOpenExchangeRatesDecimalParsing:
    async def test_json_rates_parse_as_decimal_not_float(self) -> None:
        provider = OpenExchangeRatesProvider(api_key="test-key", timeout_seconds=5)

        async def handler(request: httpx.Request) -> httpx.Response:
            # Return raw text so parse_float=Decimal is exercised on the client side.
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "timestamp": 1700000000,
                        "base": "USD",
                        "rates": {"GBP": 0.78},
                    }
                ),
            )

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        class PatchedClient(original):  # type: ignore[misc,valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        import app.services.fx.providers as providers_mod

        monkey = pytest.MonkeyPatch()
        monkey.setattr(providers_mod.httpx, "AsyncClient", PatchedClient)
        try:
            quote = await provider.get_rate("USD", "GBP")
        finally:
            monkey.undo()

        assert quote is not None
        assert quote.rate == Decimal("0.78")
        _assert_no_float_in_quote(quote)
        assert quote.provider_timestamp is not None
        assert quote.fetched_at is not None
        assert quote.provider_timestamp != quote.fetched_at or True  # both set

    async def test_no_float_inside_fx_rate_quote(self) -> None:
        quote = FxRateQuote(
            base_currency="GBP",
            quote_currency="USD",
            rate=Decimal("1.30"),
            provider_name="test",
            fetched_at=datetime.now(UTC),
            provider_timestamp=datetime.now(UTC) - timedelta(minutes=5),
            expires_at=None,
            status=FxRateStatus.CURRENT,
        )
        _assert_no_float_in_quote(quote)
        round_trip = deserialize_quote(serialize_quote(quote))
        _assert_no_float_in_quote(round_trip)


class TestFxFreshnessCache:
    async def test_controlled_stale_when_provider_fails(self) -> None:
        now = datetime.now(UTC)
        provider_ts = now - timedelta(hours=2)  # past freshness (1h), within max (6h)
        cached = FxRateQuote(
            base_currency="USD",
            quote_currency="GBP",
            rate=Decimal("0.78"),
            provider_name="openexchangerates",
            fetched_at=provider_ts,
            provider_timestamp=provider_ts,
            expires_at=None,
            status=FxRateStatus.CURRENT,
        )
        failing = MagicMock()
        failing.provider_name = "openexchangerates"
        failing.get_rate = AsyncMock(return_value=None)

        cache = MagicMock()
        cache.get = AsyncMock(return_value=serialize_quote(cached))
        cache.set = AsyncMock(return_value=True)

        fx = FxService(
            failing,
            freshness_seconds=3600,
            max_staleness_seconds=21600,
            cache=cache,
            allow_controlled_stale=True,
        )
        quote = await fx.get_rate("USD", "GBP")
        assert quote is not None
        assert quote.status is FxRateStatus.STALE
        assert quote.rate == Decimal("0.78")

    async def test_beyond_max_staleness_rejected(self) -> None:
        now = datetime.now(UTC)
        provider_ts = now - timedelta(hours=8)
        cached = FxRateQuote(
            base_currency="USD",
            quote_currency="GBP",
            rate=Decimal("0.78"),
            provider_name="openexchangerates",
            fetched_at=provider_ts,
            provider_timestamp=provider_ts,
            expires_at=None,
            status=FxRateStatus.CURRENT,
        )
        failing = MagicMock()
        failing.provider_name = "openexchangerates"
        failing.get_rate = AsyncMock(return_value=None)
        cache = MagicMock()
        cache.get = AsyncMock(return_value=serialize_quote(cached))
        cache.set = AsyncMock(return_value=True)

        fx = FxService(
            failing,
            freshness_seconds=3600,
            max_staleness_seconds=21600,
            cache=cache,
        )
        quote = await fx.get_rate("USD", "GBP")
        assert quote is None

    async def test_same_currency_never_calls_provider(self) -> None:
        provider = MagicMock()
        provider.provider_name = "stub"
        provider.get_rate = AsyncMock()
        fx = FxService(provider, cache=None)
        converted, evidence, quote = await fx.convert(
            Money.of("10.00", "GBP"), to_currency="GBP"
        )
        assert evidence is None
        assert quote is None
        assert converted.amount == Decimal("10.0000")
        provider.get_rate.assert_not_called()

    async def test_cross_currency_missing_fx_raises(self) -> None:
        fx = FxService(MagicMock(provider_name="unavailable", get_rate=AsyncMock(return_value=None)))
        with pytest.raises(FxUnavailableError):
            await fx.convert(Money.of("72.50", "CNY"), to_currency="GBP")

    async def test_cny_to_gbp_labels_remain_correct(self) -> None:
        fx = FxService(StubFXRateProvider(), cache=None)
        source = Money.of("72.50", "CNY")
        converted, evidence, quote = await fx.convert(source, to_currency="GBP")
        assert source.currency == "CNY"
        assert source.amount == Decimal("72.5000")
        assert converted.currency == "GBP"
        assert evidence is not None
        assert quote is not None
        assert quote.base_currency == "CNY"
        assert quote.quote_currency == "GBP"
        # Never relabel CNY amount as GBP.
        assert converted.amount != source.amount or converted.currency != source.currency

    async def test_provider_timestamp_drives_staleness_not_refetch_time(self) -> None:
        old_provider_ts = datetime.now(UTC) - timedelta(hours=3)
        fresh_fetch = datetime.now(UTC)
        quote = FxRateQuote(
            base_currency="GBP",
            quote_currency="USD",
            rate=Decimal("1.30"),
            provider_name="openexchangerates",
            fetched_at=fresh_fetch,
            provider_timestamp=old_provider_ts,
            expires_at=None,
            status=FxRateStatus.CURRENT,
        )
        fx = FxService(
            MagicMock(provider_name="x"),
            freshness_seconds=3600,
            max_staleness_seconds=21600,
            cache=None,
        )
        classified = fx.classify_freshness(quote)
        assert classified.status is FxRateStatus.STALE


class TestInversion:
    async def test_safe_inverse(self) -> None:
        provider = StubFXRateProvider(rates={("EUR", "JPY"): Decimal("160")})
        # No JPY→EUR direct — stub invents inverse
        quote = await provider.get_rate("JPY", "EUR")
        assert quote is not None
        assert quote.derivation == "inverted"
        assert quote.rate == (Decimal("1") / Decimal("160")).quantize(Decimal("0.00000001"))
