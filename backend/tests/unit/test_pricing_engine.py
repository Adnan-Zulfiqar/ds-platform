"""Unit tests for the pricing engine — pure functions, no database."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.exceptions import FxUnavailableError
from app.models.pricing import PricingRule, PricingScope, PricingStrategy
from app.services.pricing_engine import (
    PricingEngine,
    compute_sell_price,
    convert_currency,
    select_rule,
)

pytestmark = pytest.mark.unit


def _rule(**kwargs: object) -> PricingRule:
    rule = PricingRule()
    rule.id = uuid4()
    rule.name = "test"
    rule.scope = PricingScope.GLOBAL
    rule.strategy = PricingStrategy.PERCENTAGE_MARKUP
    rule.priority = 100
    rule.tiers = []
    rule.is_active = True
    for key, value in kwargs.items():
        setattr(rule, key, value)
    return rule


class TestComputeSellPrice:
    def test_percentage_markup(self) -> None:
        rule = _rule(markup_percent=Decimal("50"))
        assert compute_sell_price(cost=Decimal("10"), rule=rule) == Decimal("15.0000")

    def test_fixed_markup(self) -> None:
        rule = _rule(strategy=PricingStrategy.FIXED_MARKUP, markup_fixed=Decimal("3.50"))
        assert compute_sell_price(cost=Decimal("10"), rule=rule) == Decimal("13.5000")

    def test_min_profit_raises_floor(self) -> None:
        rule = _rule(markup_percent=Decimal("10"), min_profit=Decimal("5"))
        # 10 * 1.1 = 11, but min profit forces 15
        assert compute_sell_price(cost=Decimal("10"), rule=rule) == Decimal("15.0000")

    def test_max_price_caps(self) -> None:
        rule = _rule(markup_percent=Decimal("200"), max_price=Decimal("12"))
        assert compute_sell_price(cost=Decimal("10"), rule=rule) == Decimal("12.0000")

    def test_tiered_selects_matching_band(self) -> None:
        rule = _rule(
            strategy=PricingStrategy.TIERED,
            tiers=[
                {"min_cost": "0", "max_cost": "20", "markup_percent": "100"},
                {"min_cost": "20", "max_cost": None, "markup_percent": "50"},
            ],
        )
        assert compute_sell_price(cost=Decimal("10"), rule=rule) == Decimal("20.0000")
        assert compute_sell_price(cost=Decimal("30"), rule=rule) == Decimal("45.0000")


class TestSelectRule:
    def test_product_scope_beats_global(self) -> None:
        product_id = uuid4()
        global_rule = _rule(scope=PricingScope.GLOBAL, priority=999)
        product_rule = _rule(scope=PricingScope.PRODUCT, product_id=product_id, priority=1)
        chosen = select_rule(
            [global_rule, product_rule],
            product_id=product_id,
            store_id=None,
            category_id=None,
        )
        assert chosen is product_rule


class TestConvertCurrency:
    def test_same_currency_passthrough(self) -> None:
        assert convert_currency(Decimal("9.99"), from_currency="USD", to_currency="USD") == Decimal(
            "9.99"
        )

    def test_cross_currency_identity_is_forbidden(self) -> None:
        with pytest.raises(FxUnavailableError):
            convert_currency(Decimal("9.99"), from_currency="USD", to_currency="EUR")


class TestDraftVariantRow:
    async def test_profit_and_margin_use_decimal_math(self) -> None:
        engine = PricingEngine.__new__(PricingEngine)
        row = await engine._variant_row(
            variant_id=uuid4(),
            label="Black",
            is_enabled=True,
            supplier_cost=Decimal("10"),
            supplier_currency="USD",
            converted_cost=Decimal("10"),
            converted_currency="USD",
            conversion_required=False,
            conversion_type="direct",
            conversion_rate=None,
            conversion_rate_timestamp=None,
            fx_provider=None,
            fx_status=None,
            sell_price=Decimal("15"),
            compare_at_price=None,
            proposed_sell_price=None,
            shipping_cost=None,
            handling_cost=Decimal("0"),
            fee_percent=Decimal("0"),
            pricing_rule_source=None,
            row_blocked=False,
            row_block_message=None,
            allow_profit=True,
        )
        assert row.profit == Decimal("5.0000")
        assert row.margin_percent == Decimal("33.33")
        assert row.shipping_cost_available is False
        assert row.break_even_price == Decimal("10.0000")

    async def test_blocked_row_hides_profit(self) -> None:
        engine = PricingEngine.__new__(PricingEngine)
        row = await engine._variant_row(
            variant_id=uuid4(),
            label="Black",
            is_enabled=True,
            supplier_cost=Decimal("23.74"),
            supplier_currency="USD",
            converted_cost=None,
            converted_currency=None,
            conversion_required=True,
            conversion_type="unavailable",
            conversion_rate=None,
            conversion_rate_timestamp=None,
            fx_provider="unavailable",
            fx_status="unavailable",
            sell_price=Decimal("35.61"),
            compare_at_price=None,
            proposed_sell_price=None,
            shipping_cost=None,
            handling_cost=Decimal("0"),
            fee_percent=Decimal("0"),
            pricing_rule_source=None,
            row_blocked=True,
            row_block_message="Pricing cannot be calculated",
            allow_profit=False,
        )
        assert row.profit is None
        assert row.break_even_price is None
        assert row.row_blocked is True
