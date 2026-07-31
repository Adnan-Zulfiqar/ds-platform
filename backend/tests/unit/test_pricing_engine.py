"""Unit tests for the pricing engine — pure functions, no database."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.pricing import PricingRule, PricingScope, PricingStrategy
from app.services.pricing_engine import compute_sell_price, convert_currency, select_rule

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

    def test_currency_hook_is_identity(self) -> None:
        assert convert_currency(Decimal("9.99"), from_currency="USD", to_currency="EUR") == Decimal(
            "9.99"
        )
