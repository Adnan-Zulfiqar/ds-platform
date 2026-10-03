"""Track E2 (M24C) — a sale fee taken from the selling price.

The merchant's markup, target margin and profit floors must still hold
*after* the marketplace or payment fee is deducted. Pure arithmetic: no
database, no session.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.models.pricing import PricingStrategy
from app.services.pricing_engine import calculate_price, compute_sell_price
from tests.unit.test_pricing_calculation import make_product, make_rule

pytestmark = pytest.mark.unit

D = Decimal


def test_no_fee_changes_nothing() -> None:
    rule = make_rule(markup_percent=D("50"))
    assert compute_sell_price(cost=D("10"), rule=rule) == D("15.00")
    assert compute_sell_price(
        cost=D("10"), rule=make_rule(markup_percent=D("50"), sale_fee_percent=D("0"))
    ) == D("15.00")


def test_markup_survives_the_fee() -> None:
    rule = make_rule(markup_percent=D("50"), sale_fee_percent=D("20"))
    price = compute_sell_price(cost=D("10"), rule=rule)
    assert price == D("18.75")  # 15 / 0.8
    assert price * D("0.8") == D("15.000")  # after the fee, the 50% markup is intact


def test_target_margin_is_net_of_the_fee() -> None:
    rule = make_rule(
        strategy=PricingStrategy.TARGET_MARGIN, margin_percent=D("30"), sale_fee_percent=D("10")
    )
    price = compute_sell_price(cost=D("12"), rule=rule)
    assert price == D("20.00")  # 12 / (1 - 0.3 - 0.1)
    assert price - price * D("0.1") - D("12") == D("6.000")  # 30% of 20


def test_margin_plus_fee_of_100_or_more_is_refused() -> None:
    rule = make_rule(
        strategy=PricingStrategy.TARGET_MARGIN, margin_percent=D("80"), sale_fee_percent=D("20")
    )
    with pytest.raises(ValidationError):
        compute_sell_price(cost=D("10"), rule=rule)


def test_the_profit_floor_is_net_of_the_fee() -> None:
    rule = make_rule(
        strategy=PricingStrategy.FIXED_MARKUP,
        markup_fixed=D("0"),
        min_profit=D("5"),
        sale_fee_percent=D("50"),
    )
    price = compute_sell_price(cost=D("10"), rule=rule)
    assert price == D("30.00")  # (10 + 5) / 0.5
    assert price * D("0.5") - D("10") == D("5.00")


def test_profit_and_margin_are_reported_net_of_the_fee() -> None:
    rule = make_rule(markup_percent=D("50"), sale_fee_percent=D("20"))
    calc = calculate_price(make_product(cost="10.00"), rule=rule)
    assert calc.price == D("18.75")
    assert calc.sale_fee == D("3.75")
    assert calc.profit == D("5.00")  # 18.75 - 3.75 - 10
    assert calc.margin_percent == D("26.67")


@pytest.mark.parametrize("fee", [D("-1"), D("100"), D("150")])
def test_an_impossible_fee_is_refused(fee: Decimal) -> None:
    with pytest.raises(ValidationError):
        compute_sell_price(
            cost=D("10"), rule=make_rule(markup_percent=D("10"), sale_fee_percent=fee)
        )
