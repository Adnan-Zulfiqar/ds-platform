"""Step 13 — Shopify publish must never send a price in the wrong currency.

`ShopifySyncService._assert_variant_prices_match_store_currency` is a pure
validation function over already-loaded `Product`/`Store` objects, so this
is a plain unit test (no DB, no Shopify API) — the same shape
`test_m24a_currency_fx.py` already uses for `PricingEngine`'s currency
authority checks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.integrations.shopify.sync import ShopifySyncService
from app.models.store import StorePlatform

pytestmark = pytest.mark.unit


class _Variant:
    def __init__(
        self,
        *,
        sell_price: Decimal | None,
        sell_price_currency: str | None,
        is_enabled: bool = True,
        deleted_at: datetime | None = None,
        label: str = "Variant",
        external_variant_id: str = "sku-1",
    ) -> None:
        self.id = uuid.uuid4()
        self.sell_price = sell_price
        self.sell_price_currency = sell_price_currency
        self.is_enabled = is_enabled
        self.deleted_at = deleted_at
        self.label = label
        self.external_variant_id = external_variant_id


class _Product:
    def __init__(self, *, variants: list[_Variant]) -> None:
        self.variants = variants


class _Store:
    def __init__(
        self,
        *,
        platform: StorePlatform = StorePlatform.SHOPIFY,
        currency: str | None,
        currency_last_synced_at: datetime | None,
    ) -> None:
        self.platform = platform
        self.currency = currency
        self.currency_last_synced_at = currency_last_synced_at


def _service() -> ShopifySyncService:
    return ShopifySyncService.__new__(ShopifySyncService)


class TestBlocksOnMismatch:
    def test_gbp_priced_variant_blocks_publish_to_a_usd_store(self) -> None:
        product = _Product(
            variants=[_Variant(sell_price=Decimal("6.60"), sell_price_currency="GBP")]
        )
        store = _Store(currency="USD", currency_last_synced_at=datetime.now(UTC))

        with pytest.raises(ValidationError) as exc:
            _service()._assert_variant_prices_match_store_currency(product=product, store=store)
        assert "GBP" in str(exc.value)
        assert "USD" in str(exc.value)

    def test_an_unstamped_sell_price_blocks_publish(self) -> None:
        """Pre-migration-0021 data, or a price set some other way, with no
        recorded currency at all -- must not be assumed safe."""
        product = _Product(
            variants=[_Variant(sell_price=Decimal("40.00"), sell_price_currency=None)]
        )
        store = _Store(currency="USD", currency_last_synced_at=datetime.now(UTC))

        with pytest.raises(ValidationError) as exc:
            _service()._assert_variant_prices_match_store_currency(product=product, store=store)
        assert "unrecorded currency" in str(exc.value) or "USD" in str(exc.value)


class TestAllowsOnMatch:
    def test_matching_currency_does_not_raise(self) -> None:
        product = _Product(
            variants=[_Variant(sell_price=Decimal("9.49"), sell_price_currency="USD")]
        )
        store = _Store(currency="USD", currency_last_synced_at=datetime.now(UTC))

        # Must not raise.
        _service()._assert_variant_prices_match_store_currency(product=product, store=store)

    def test_currency_comparison_is_case_and_whitespace_insensitive(self) -> None:
        product = _Product(
            variants=[_Variant(sell_price=Decimal("9.49"), sell_price_currency="usd")]
        )
        store = _Store(currency=" USD ", currency_last_synced_at=datetime.now(UTC))

        _service()._assert_variant_prices_match_store_currency(product=product, store=store)


class TestScoping:
    def test_a_variant_with_no_sell_price_is_not_checked(self) -> None:
        """Relies on the pre-existing list_price/product.sell_price publish
        fallback -- unchanged by this fix, deliberately out of scope."""
        product = _Product(variants=[_Variant(sell_price=None, sell_price_currency=None)])
        store = _Store(currency="USD", currency_last_synced_at=datetime.now(UTC))

        _service()._assert_variant_prices_match_store_currency(product=product, store=store)

    def test_a_disabled_variant_is_not_checked(self) -> None:
        product = _Product(
            variants=[
                _Variant(
                    sell_price=Decimal("6.60"),
                    sell_price_currency="GBP",
                    is_enabled=False,
                )
            ]
        )
        store = _Store(currency="USD", currency_last_synced_at=datetime.now(UTC))

        _service()._assert_variant_prices_match_store_currency(product=product, store=store)

    def test_a_soft_deleted_variant_is_not_checked(self) -> None:
        product = _Product(
            variants=[
                _Variant(
                    sell_price=Decimal("6.60"),
                    sell_price_currency="GBP",
                    deleted_at=datetime.now(UTC),
                )
            ]
        )
        store = _Store(currency="USD", currency_last_synced_at=datetime.now(UTC))

        _service()._assert_variant_prices_match_store_currency(product=product, store=store)

    def test_an_unverified_store_currency_is_not_checked(self) -> None:
        """The store's own currency has never been confirmed via Shopify
        sync -- a separate, pre-existing gap (M17-adjacent) this check does
        not attempt to close; nothing to compare against yet."""
        product = _Product(
            variants=[_Variant(sell_price=Decimal("6.60"), sell_price_currency="GBP")]
        )
        store = _Store(currency="USD", currency_last_synced_at=None)

        _service()._assert_variant_prices_match_store_currency(product=product, store=store)

    def test_a_non_shopify_store_is_not_checked(self) -> None:
        product = _Product(
            variants=[_Variant(sell_price=Decimal("6.60"), sell_price_currency="GBP")]
        )
        store = _Store(
            platform=StorePlatform.MANUAL, currency="USD", currency_last_synced_at=datetime.now(UTC)
        )

        _service()._assert_variant_prices_match_store_currency(product=product, store=store)
