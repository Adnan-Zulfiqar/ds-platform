"""M3A — rule precedence and supplier-shipping selection.

Precedence is the part of this milestone a merchant is most likely to be
surprised by, so the tests here are exhaustive rather than representative:
every scope is asserted to beat every broader scope, individually.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import ClassVar

import pytest

from app.models.pricing import (
    PricingRule,
    PricingScope,
    PricingStrategy,
    ShippingNoMatchBehaviour,
    ShippingRule,
    ShippingSelectionStrategy,
)
from app.services.rule_resolution import resolve_pricing_rule, resolve_shipping_rule
from app.services.shipping_rules import (
    REVIEW_DESTINATION_UNKNOWN,
    REVIEW_FELL_BACK,
    REVIEW_NO_MATCH,
    REVIEW_NO_QUOTES,
    ShippingQuote,
    select_shipping,
)

pytestmark = pytest.mark.unit

PRODUCT = uuid.uuid4()
VARIANT = uuid.uuid4()
STORE = uuid.uuid4()
CATEGORY = "electronics"


def pricing_rule(scope: PricingScope, *, priority: int = 100, name: str = "r") -> PricingRule:
    return PricingRule(
        name=name,
        scope=scope,
        priority=priority,
        strategy=PricingStrategy.PERCENTAGE_MARKUP,
        markup_percent=Decimal("50"),
        variant_id=VARIANT if scope is PricingScope.VARIANT else None,
        product_id=PRODUCT if scope in (PricingScope.PRODUCT, PricingScope.VARIANT) else None,
        store_id=STORE if scope is PricingScope.STORE else None,
        category_id=CATEGORY if scope is PricingScope.CATEGORY else None,
        tiers=[],
        version=3,
    )


def shipping_rule(**overrides: object) -> ShippingRule:
    base: dict[str, object] = {
        "name": "ship",
        "scope": PricingScope.GLOBAL,
        "priority": 100,
        "destination_country": "GB",
        "selection_strategy": ShippingSelectionStrategy.CHEAPEST,
        "max_delivery_days": None,
        "max_shipping_cost": None,
        "tracking_required": False,
        "preferred_carriers": [],
        "blocked_carriers": [],
        "no_match_behaviour": ShippingNoMatchBehaviour.NEEDS_REVIEW,
        "version": 1,
    }
    base.update(overrides)
    return ShippingRule(**base)


def resolve(*scopes: PricingScope):
    return resolve_pricing_rule(
        [pricing_rule(s) for s in scopes],
        product_id=PRODUCT,
        variant_id=VARIANT,
        store_id=STORE,
        category_id=CATEGORY,
    )


class TestPricingPrecedence:
    @pytest.mark.parametrize(
        "loser",
        [PricingScope.PRODUCT, PricingScope.CATEGORY, PricingScope.STORE, PricingScope.GLOBAL],
    )
    def test_a_variant_override_beats_every_broader_scope(self, loser: PricingScope) -> None:
        assert resolve(loser, PricingScope.VARIANT).scope is PricingScope.VARIANT

    @pytest.mark.parametrize(
        "loser", [PricingScope.CATEGORY, PricingScope.STORE, PricingScope.GLOBAL]
    )
    def test_a_product_override_beats_every_broader_scope(self, loser: PricingScope) -> None:
        assert resolve(loser, PricingScope.PRODUCT).scope is PricingScope.PRODUCT

    @pytest.mark.parametrize("loser", [PricingScope.STORE, PricingScope.GLOBAL])
    def test_a_category_rule_beats_store_and_global(self, loser: PricingScope) -> None:
        assert resolve(loser, PricingScope.CATEGORY).scope is PricingScope.CATEGORY

    def test_a_store_rule_beats_global(self) -> None:
        assert resolve(PricingScope.GLOBAL, PricingScope.STORE).scope is PricingScope.STORE

    def test_global_applies_when_nothing_narrower_exists(self) -> None:
        assert resolve(PricingScope.GLOBAL).scope is PricingScope.GLOBAL

    def test_the_full_stack_resolves_to_the_variant(self) -> None:
        resolution = resolve(
            PricingScope.GLOBAL,
            PricingScope.STORE,
            PricingScope.CATEGORY,
            PricingScope.PRODUCT,
            PricingScope.VARIANT,
        )
        assert resolution.scope is PricingScope.VARIANT
        # The whole losing chain is reported, so the UI can show inheritance.
        assert len(resolution.overridden) == 4

    def test_priority_breaks_a_tie_within_one_scope_and_says_so(self) -> None:
        low = pricing_rule(PricingScope.GLOBAL, priority=10, name="low")
        high = pricing_rule(PricingScope.GLOBAL, priority=90, name="high")
        resolution = resolve_pricing_rule(
            [low, high], product_id=PRODUCT, store_id=STORE, category_id=CATEGORY
        )
        assert resolution.rule is high
        assert "Priority 90" in resolution.reason

    def test_the_resolution_reports_scope_version_and_a_reason(self) -> None:
        resolution = resolve(PricingScope.GLOBAL)
        assert resolution.scope is PricingScope.GLOBAL
        assert resolution.version == 3
        assert resolution.reason


class TestNonMatching:
    def test_a_rule_for_another_product_does_not_apply(self) -> None:
        other = pricing_rule(PricingScope.PRODUCT)
        other.product_id = uuid.uuid4()
        assert resolve_pricing_rule([other], product_id=PRODUCT).rule is None

    def test_a_rule_for_another_store_does_not_apply(self) -> None:
        rule = pricing_rule(PricingScope.STORE)
        assert resolve_pricing_rule([rule], product_id=PRODUCT, store_id=uuid.uuid4()).rule is None

    def test_a_variant_rule_does_not_apply_when_no_variant_is_being_priced(self) -> None:
        rule = pricing_rule(PricingScope.VARIANT)
        assert resolve_pricing_rule([rule], product_id=PRODUCT, variant_id=None).rule is None

    def test_no_rule_is_a_normal_state_with_an_explanation(self) -> None:
        resolution = resolve_pricing_rule([], product_id=PRODUCT)
        assert not resolution.found
        assert "keeps its current price" in resolution.reason

    def test_a_rule_never_matches_on_its_name(self) -> None:
        """A rule applies because of an id, never because of a word. Naming a
        product-scoped rule after the product must not make it apply --
        "match the variant called Large" is a tempting shortcut that silently
        mis-prices every product whose options use the same word."""
        named_but_unbound = pricing_rule(PricingScope.PRODUCT, name="Large")
        named_but_unbound.product_id = None
        assert resolve_pricing_rule([named_but_unbound], product_id=PRODUCT).rule is None


class TestShippingRulePrecedence:
    def test_shipping_precedence_matches_pricing_precedence(self) -> None:
        narrow = shipping_rule(scope=PricingScope.PRODUCT, product_id=PRODUCT, name="p")
        broad = shipping_rule(scope=PricingScope.GLOBAL, name="g")
        resolution = resolve_shipping_rule([broad, narrow], product_id=PRODUCT)
        assert resolution.rule is narrow
        assert resolution.scope is PricingScope.PRODUCT


class TestShippingSelection:
    QUOTES: ClassVar[list[ShippingQuote]] = [
        ShippingQuote("Economy", Decimal("2.00"), delivery_days=45, tracked=False, carrier="AliX"),
        ShippingQuote("Standard", Decimal("5.00"), delivery_days=20, tracked=True, carrier="AliX"),
        ShippingQuote("Express", Decimal("14.00"), delivery_days=6, tracked=True, carrier="DHL"),
    ]

    def test_cheapest_picks_the_lowest_cost(self) -> None:
        chosen = select_shipping(
            self.QUOTES, rule=shipping_rule(selection_strategy=ShippingSelectionStrategy.CHEAPEST)
        )
        assert chosen.quote is not None and chosen.quote.service_name == "Economy"

    def test_cheapest_tracked_prefers_a_tracked_service(self) -> None:
        chosen = select_shipping(
            self.QUOTES,
            rule=shipping_rule(selection_strategy=ShippingSelectionStrategy.CHEAPEST_TRACKED),
        )
        assert chosen.quote is not None and chosen.quote.service_name == "Standard"

    def test_fastest_picks_the_shortest_delivery(self) -> None:
        chosen = select_shipping(
            self.QUOTES, rule=shipping_rule(selection_strategy=ShippingSelectionStrategy.FASTEST)
        )
        assert chosen.quote is not None and chosen.quote.service_name == "Express"

    def test_fastest_under_cost_respects_the_ceiling(self) -> None:
        chosen = select_shipping(
            self.QUOTES,
            rule=shipping_rule(
                selection_strategy=ShippingSelectionStrategy.FASTEST_UNDER_COST,
                max_shipping_cost=Decimal("6.00"),
            ),
        )
        assert chosen.quote is not None and chosen.quote.service_name == "Standard"

    def test_maximum_delivery_days_excludes_slower_services(self) -> None:
        chosen = select_shipping(self.QUOTES, rule=shipping_rule(max_delivery_days=10))
        assert chosen.quote is not None and chosen.quote.service_name == "Express"

    def test_maximum_shipping_cost_excludes_dearer_services(self) -> None:
        chosen = select_shipping(self.QUOTES, rule=shipping_rule(max_shipping_cost=Decimal("3.00")))
        assert chosen.quote is not None and chosen.quote.service_name == "Economy"

    def test_tracking_required_excludes_untracked_services(self) -> None:
        chosen = select_shipping(self.QUOTES, rule=shipping_rule(tracking_required=True))
        assert chosen.quote is not None and chosen.quote.tracked is True

    def test_tracking_required_rejects_an_unknown_tracking_status(self) -> None:
        """An unstated tracking status is not a "yes". Treating it as one is
        how a tracked-only catalogue quietly ships untracked."""
        unknown = [ShippingQuote("Mystery", Decimal("1.00"), delivery_days=30, tracked=None)]
        chosen = select_shipping(unknown, rule=shipping_rule(tracking_required=True))
        assert chosen.quote is None
        assert REVIEW_NO_MATCH in chosen.review_reasons

    def test_preferred_carriers_restrict_the_pool(self) -> None:
        chosen = select_shipping(self.QUOTES, rule=shipping_rule(preferred_carriers=["DHL"]))
        assert chosen.quote is not None and chosen.quote.carrier == "DHL"

    def test_blocked_carriers_are_excluded(self) -> None:
        chosen = select_shipping(self.QUOTES, rule=shipping_rule(blocked_carriers=["alix"]))
        assert chosen.quote is not None and chosen.quote.carrier == "DHL"

    def test_an_empty_preferred_list_means_no_preference_not_nothing_allowed(self) -> None:
        chosen = select_shipping(self.QUOTES, rule=shipping_rule(preferred_carriers=[]))
        assert chosen.quote is not None


class TestShippingFailsClosed:
    def test_no_match_flags_review_with_a_specific_reason(self) -> None:
        chosen = select_shipping(
            [ShippingQuote("Slow", Decimal("2.00"), delivery_days=60, tracked=False)],
            rule=shipping_rule(tracking_required=True, max_delivery_days=5),
        )
        assert chosen.quote is None
        assert chosen.needs_review
        assert REVIEW_NO_MATCH in chosen.review_reasons
        assert "tracked" in chosen.explanation and "5 days" in chosen.explanation

    def test_block_publish_behaviour_sets_the_publish_block(self) -> None:
        chosen = select_shipping(
            [ShippingQuote("Slow", Decimal("2.00"), delivery_days=60)],
            rule=shipping_rule(
                max_delivery_days=5, no_match_behaviour=ShippingNoMatchBehaviour.BLOCK_PUBLISH
            ),
        )
        assert chosen.blocks_publish is True

    def test_cheapest_available_fallback_is_still_flagged(self) -> None:
        """A fallback that satisfied nobody's constraint is exactly the case
        the merchant has to be told about."""
        chosen = select_shipping(
            [ShippingQuote("Slow", Decimal("2.00"), delivery_days=60)],
            rule=shipping_rule(
                max_delivery_days=5,
                no_match_behaviour=ShippingNoMatchBehaviour.CHEAPEST_AVAILABLE,
            ),
        )
        assert chosen.quote is not None
        assert REVIEW_FELL_BACK in chosen.review_reasons

    def test_no_quotes_at_all_is_flagged(self) -> None:
        chosen = select_shipping([], rule=shipping_rule())
        assert REVIEW_NO_QUOTES in chosen.review_reasons

    def test_an_unpriced_quote_is_not_treated_as_free(self) -> None:
        chosen = select_shipping(
            [ShippingQuote("Unpriced", None, delivery_days=10)], rule=shipping_rule()
        )
        assert chosen.quote is None
        assert REVIEW_NO_QUOTES in chosen.review_reasons

    def test_an_unknown_destination_is_never_silently_defaulted(self) -> None:
        chosen = select_shipping(
            [ShippingQuote("Standard", Decimal("5.00"))],
            rule=shipping_rule(destination_country=None),
            destination_country=None,
        )
        assert chosen.quote is None
        assert REVIEW_DESTINATION_UNKNOWN in chosen.review_reasons

    def test_the_import_destination_is_used_when_the_rule_has_none(self) -> None:
        chosen = select_shipping(
            [ShippingQuote("Standard", Decimal("5.00"))],
            rule=shipping_rule(destination_country=None),
            destination_country="DE",
        )
        assert chosen.quote is not None


class TestNoShippingRuleConfigured:
    def test_the_cheapest_option_is_used_and_nothing_is_flagged(self) -> None:
        """A tenant that has not opted into shipping rules must not suddenly
        start seeing review flags -- that is the pre-M3A behaviour."""
        chosen = select_shipping(
            [
                ShippingQuote("A", Decimal("9.00")),
                ShippingQuote("B", Decimal("3.00")),
            ],
            rule=None,
        )
        assert chosen.quote is not None and chosen.quote.service_name == "B"
        assert not chosen.needs_review
