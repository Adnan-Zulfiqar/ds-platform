"""M3A — the pricing calculation layer.

Pure arithmetic, no database: these are the tests that decide whether a
merchant's catalogue sells at a profit, so they need to be fast enough that
nobody is tempted to skip them.

Every expected value here was derived by hand from the formula in the brief,
not by recording what the implementation returned.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.models.pricing import (
    PriceRounding,
    PricingRule,
    PricingStrategy,
    ShippingCostHandling,
)
from app.models.product import Product, ProductSource, ProductStatus
from app.services.pricing_engine import (
    REVIEW_SHIPPING_COST_UNKNOWN,
    REVIEW_SUPPLIER_COST_UNKNOWN,
    REVIEW_SUPPLIER_CURRENCY_UNKNOWN,
    apply_rounding,
    calculate_price,
    compute_compare_at_price,
    compute_sell_price,
    landed_cost,
)

pytestmark = pytest.mark.unit


def make_rule(**overrides: object) -> PricingRule:
    """A transient rule. Never added to a session, so no tenant is needed."""
    base: dict[str, object] = {
        "name": "test",
        "strategy": PricingStrategy.PERCENTAGE_MARKUP,
        "markup_percent": None,
        "markup_fixed": None,
        "margin_percent": None,
        "min_profit": None,
        "min_profit_per_variant": None,
        "min_price": None,
        "max_price": None,
        "duty_percent": None,
        "fees_fixed": None,
        "sale_fee_percent": None,
        "rounding": PriceRounding.NONE,
        "compare_at_percent": None,
        "shipping_cost_handling": ShippingCostHandling.INCLUDE_IN_PRICE,
        "tiers": [],
    }
    base.update(overrides)
    return PricingRule(**base)


def make_product(
    *,
    cost: str | None = "10.00",
    shipping: str | None = "0.00",
    currency: str | None = "USD",
) -> Product:
    return Product(
        source=ProductSource.MANUAL,
        external_id="m3a-test",
        title="Test product",
        status=ProductStatus.DRAFT,
        cost_price_min=None if cost is None else Decimal(cost),
        shipping_cost=None if shipping is None else Decimal(shipping),
        currency=currency,
    )


class TestStrategies:
    """The four formulas, each on a 10.00 landed cost."""

    def test_fixed_profit_adds_a_flat_amount(self) -> None:
        price = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(strategy=PricingStrategy.FIXED_MARKUP, markup_fixed=Decimal("8")),
        )
        assert price == Decimal("18.00")

    def test_markup_percentage_multiplies_the_cost(self) -> None:
        price = compute_sell_price(
            cost=Decimal("10.00"), rule=make_rule(markup_percent=Decimal("50"))
        )
        assert price == Decimal("15.00")

    def test_target_margin_divides_and_is_not_the_same_as_markup(self) -> None:
        """The confusion this milestone's UI exists to prevent: at 50% a
        10.00 cost is 15.00 as markup but 20.00 as margin."""
        margin = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(strategy=PricingStrategy.TARGET_MARGIN, margin_percent=Decimal("50")),
        )
        markup = compute_sell_price(
            cost=Decimal("10.00"), rule=make_rule(markup_percent=Decimal("50"))
        )
        assert margin == Decimal("20.00")
        assert markup == Decimal("15.00")
        assert margin != markup

    def test_hybrid_applies_both_components(self) -> None:
        price = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(
                strategy=PricingStrategy.HYBRID,
                markup_percent=Decimal("50"),
                markup_fixed=Decimal("3"),
            ),
        )
        assert price == Decimal("18.00")

    @pytest.mark.parametrize("margin", [Decimal("100"), Decimal("120")])
    def test_a_margin_of_100_or_more_is_rejected_rather_than_dividing_by_zero(
        self, margin: Decimal
    ) -> None:
        with pytest.raises(ValidationError):
            compute_sell_price(
                cost=Decimal("10.00"),
                rule=make_rule(strategy=PricingStrategy.TARGET_MARGIN, margin_percent=margin),
            )

    @pytest.mark.parametrize(
        "strategy",
        [PricingStrategy.PERCENTAGE_MARKUP, PricingStrategy.FIXED_MARKUP],
    )
    def test_a_strategy_without_its_input_is_rejected(self, strategy: PricingStrategy) -> None:
        with pytest.raises(ValidationError):
            compute_sell_price(cost=Decimal("10.00"), rule=make_rule(strategy=strategy))


class TestGuardrails:
    def test_minimum_profit_lifts_a_price_that_would_earn_less(self) -> None:
        price = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(markup_percent=Decimal("5"), min_profit=Decimal("4")),
        )
        assert price == Decimal("14.00")

    def test_minimum_price_is_independent_of_cost(self) -> None:
        price = compute_sell_price(
            cost=Decimal("1.00"),
            rule=make_rule(markup_percent=Decimal("10"), min_price=Decimal("9.50")),
        )
        assert price == Decimal("9.50")

    def test_maximum_price_caps_a_higher_result(self) -> None:
        price = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(markup_percent=Decimal("500"), max_price=Decimal("30")),
        )
        assert price == Decimal("30.00")

    def test_a_maximum_beats_a_minimum_when_they_conflict(self) -> None:
        """An explicit ceiling is a merchant decision; a computed floor is a
        derived one. The ceiling wins, and the caller sees an impossible
        combination rather than a silently ignored cap."""
        price = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(
                markup_percent=Decimal("10"),
                min_profit=Decimal("50"),
                max_price=Decimal("20"),
            ),
        )
        assert price == Decimal("20.00")


class TestRounding:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("20.00", "19.99"),
            ("19.99", "19.99"),
            ("20.49", "19.99"),  # exact tie resolves downward
            ("20.50", "20.99"),
            ("1.00", "0.99"),
        ],
    )
    def test_charm_rounding_lands_on_the_nearest_ending_below(
        self, value: str, expected: str
    ) -> None:
        assert apply_rounding(Decimal(value), PriceRounding.NINETY_NINE) == Decimal(expected)

    @pytest.mark.parametrize("value", ["0.45", "0.30", "0.01"])
    def test_a_sub_unit_price_is_never_rounded_to_a_negative(self, value: str) -> None:
        """The defect the partial M3A commit shipped: `whole - 1 + 0.99` on a
        0.45 price produced **-0.01**, a negative selling price reaching
        storage. There is no charm ending at or below such a price, so the
        price is left alone rather than made negative or silently raised."""
        rounded = apply_rounding(Decimal(value), PriceRounding.NINETY_NINE)
        assert rounded == Decimal(value)
        assert rounded > 0

    def test_the_same_defect_end_to_end(self) -> None:
        price = compute_sell_price(
            cost=Decimal("0.30"),
            rule=make_rule(markup_percent=Decimal("50"), rounding=PriceRounding.NINETY_NINE),
        )
        assert price > 0

    def test_ninety_five_rounding(self) -> None:
        assert apply_rounding(Decimal("20.00"), PriceRounding.NINETY_FIVE) == Decimal("19.95")

    @pytest.mark.parametrize(("value", "expected"), [("20.49", "20"), ("20.50", "21")])
    def test_whole_number_rounding(self, value: str, expected: str) -> None:
        assert apply_rounding(Decimal(value), PriceRounding.WHOLE) == Decimal(expected)

    def test_no_rounding_leaves_the_price_exact(self) -> None:
        assert apply_rounding(Decimal("20.37"), PriceRounding.NONE) == Decimal("20.37")

    def test_rounding_never_breaks_a_profit_floor(self) -> None:
        """Rounding runs last, so it can land under a floor. When it does the
        price steps up to the next charm ending rather than the floor itself,
        so the result still reads as a charm price."""
        price = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(
                strategy=PricingStrategy.TARGET_MARGIN,
                margin_percent=Decimal("50"),
                min_profit=Decimal("10"),
                rounding=PriceRounding.NINETY_NINE,
            ),
        )
        assert price == Decimal("20.99")
        assert price - Decimal("10.00") >= Decimal("10")

    def test_a_rounding_repair_never_crosses_a_ceiling(self) -> None:
        price = compute_sell_price(
            cost=Decimal("10.00"),
            rule=make_rule(
                strategy=PricingStrategy.TARGET_MARGIN,
                margin_percent=Decimal("50"),
                min_profit=Decimal("10"),
                rounding=PriceRounding.NINETY_NINE,
                max_price=Decimal("20.50"),
            ),
        )
        assert price == Decimal("20.50")


class TestCompareAtPrice:
    def test_compare_at_is_a_percentage_above_the_selling_price(self) -> None:
        assert compute_compare_at_price(
            Decimal("20.00"), make_rule(compare_at_percent=Decimal("25"))
        ) == Decimal("25.00")

    def test_compare_at_is_absent_when_unconfigured(self) -> None:
        assert compute_compare_at_price(Decimal("20.00"), make_rule()) is None

    @pytest.mark.parametrize("percent", [Decimal("0"), Decimal("-10")])
    def test_a_non_positive_compare_at_is_treated_as_unset(self, percent: Decimal) -> None:
        assert (
            compute_compare_at_price(Decimal("20"), make_rule(compare_at_percent=percent)) is None
        )


class TestLandedCost:
    def test_supplier_shipping_is_part_of_landed_cost_by_default(self) -> None:
        landed = landed_cost(make_product(cost="10", shipping="4"), rule=make_rule())
        assert landed.amount == Decimal("14.00")
        assert landed.profit_basis == Decimal("14.00")
        assert not landed.needs_review

    def test_known_duty_and_fees_are_included(self) -> None:
        landed = landed_cost(
            make_product(cost="10", shipping="4"),
            rule=make_rule(duty_percent=Decimal("5"), fees_fixed=Decimal("1")),
        )
        # (10 + 4) * 5% = 0.70, plus 1.00 flat
        assert landed.fees == Decimal("1.70")
        assert landed.amount == Decimal("15.70")

    def test_shipping_charged_separately_leaves_the_item_price_alone(self) -> None:
        landed = landed_cost(
            make_product(cost="10", shipping="4"),
            rule=make_rule(shipping_cost_handling=ShippingCostHandling.CHARGE_SEPARATELY),
        )
        assert landed.amount == Decimal("10.00")
        assert landed.separate_shipping_charge == Decimal("4")
        # Profit is still measured against everything actually paid.
        assert landed.profit_basis == Decimal("14.00")

    def test_shipping_absorbed_from_profit_prices_as_if_free_but_reports_honestly(self) -> None:
        landed = landed_cost(
            make_product(cost="10", shipping="4"),
            rule=make_rule(shipping_cost_handling=ShippingCostHandling.ABSORB_FROM_PROFIT),
        )
        assert landed.amount == Decimal("10.00")
        assert landed.separate_shipping_charge is None
        assert landed.profit_basis == Decimal("14.00")

    def test_absorbed_shipping_shows_up_as_reduced_profit(self) -> None:
        calc = calculate_price(
            make_product(cost="10", shipping="4"),
            rule=make_rule(
                markup_percent=Decimal("50"),
                shipping_cost_handling=ShippingCostHandling.ABSORB_FROM_PROFIT,
            ),
        )
        assert calc.price == Decimal("15.00")
        # 15.00 sale against 14.00 truly paid, not the 10.00 it was priced on.
        assert calc.profit == Decimal("1.00")


class TestMissingDataIsNeverZero:
    def test_a_missing_supplier_cost_produces_no_price(self) -> None:
        calc = calculate_price(
            make_product(cost=None), rule=make_rule(markup_percent=Decimal("50"))
        )
        assert calc.price is None
        assert calc.needs_review
        assert REVIEW_SUPPLIER_COST_UNKNOWN in calc.review_reasons

    def test_a_missing_shipping_cost_produces_no_price(self) -> None:
        calc = calculate_price(
            make_product(shipping=None), rule=make_rule(markup_percent=Decimal("50"))
        )
        assert calc.price is None
        assert REVIEW_SHIPPING_COST_UNKNOWN in calc.review_reasons

    def test_an_unknown_currency_is_flagged(self) -> None:
        calc = calculate_price(
            make_product(currency=None), rule=make_rule(markup_percent=Decimal("50"))
        )
        assert REVIEW_SUPPLIER_CURRENCY_UNKNOWN in calc.review_reasons

    def test_no_matching_rule_produces_no_price_but_is_not_a_data_problem(self) -> None:
        """No governing rule is a legitimate state, not a defect: the product
        simply keeps whatever price it already has. It must not be flagged
        `Needs review`, which is reserved for *missing supplier data* --
        conflating the two would bury the products that genuinely need a
        human under every product nobody wrote a rule for."""
        calc = calculate_price(make_product(), rule=None)
        assert calc.price is None
        assert not calc.needs_review
        assert calc.review_reasons == ()

    def test_the_reason_names_the_exact_missing_field(self) -> None:
        """A merchant needs to know *which* number is missing, not merely
        that something is."""
        calc = calculate_price(
            make_product(cost=None, shipping=None, currency=None),
            rule=make_rule(markup_percent=Decimal("50")),
        )
        assert set(calc.review_reasons) == {
            REVIEW_SUPPLIER_COST_UNKNOWN,
            REVIEW_SHIPPING_COST_UNKNOWN,
            REVIEW_SUPPLIER_CURRENCY_UNKNOWN,
        }


class TestReportedFigures:
    def test_markup_and_margin_are_reported_as_different_numbers(self) -> None:
        calc = calculate_price(
            make_product(cost="10", shipping="4"),
            rule=make_rule(strategy=PricingStrategy.TARGET_MARGIN, margin_percent=Decimal("50")),
        )
        assert calc.price == Decimal("28.00")
        assert calc.profit == Decimal("14.00")
        assert calc.margin_percent == Decimal("50.00")
        assert calc.markup_percent == Decimal("100.00")

    def test_per_variant_profit_floor_is_measured_against_what_is_actually_paid(self) -> None:
        calc = calculate_price(
            make_product(cost="10", shipping="4"),
            rule=make_rule(
                markup_percent=Decimal("1"),
                min_profit_per_variant=Decimal("6"),
                shipping_cost_handling=ShippingCostHandling.ABSORB_FROM_PROFIT,
            ),
        )
        assert calc.price is not None
        assert calc.profit is not None
        assert calc.profit >= Decimal("6")


class TestDecimalSafety:
    def test_no_binary_floating_point_reaches_a_money_value(self) -> None:
        """0.1 + 0.2 famously is not 0.3 in binary floating point. Every
        persisted money figure here is a Decimal, so this holds exactly."""
        calc = calculate_price(
            make_product(cost="0.10", shipping="0.20"),
            rule=make_rule(strategy=PricingStrategy.FIXED_MARKUP, markup_fixed=Decimal("0.70")),
        )
        assert calc.landed.amount == Decimal("0.30")
        assert calc.price == Decimal("1.00")
        assert isinstance(calc.price, Decimal)
