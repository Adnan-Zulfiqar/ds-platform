"""Choosing one supplier shipping option from the quotes available (M3A).

This decides **which shipping service we buy from the supplier**, and
therefore what shipping costs us. It does not touch Shopify delivery
profiles: the existing integration has no safe path for editing checkout
delivery, and letting a settings save change live checkout behaviour is not
something to introduce as a side effect of a pricing milestone.

The whole module fails closed. Every path that cannot honour the merchant's
constraints returns a rejection with a specific reason rather than quietly
falling back to whatever was cheapest -- a silent fallback is how an
"expedited, tracked" catalogue ends up on an untracked 60-day service.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.pricing import (
    ShippingNoMatchBehaviour,
    ShippingRule,
    ShippingSelectionStrategy,
)

#: Review reasons this module can raise. Strings for the same reason the
#: pricing ones are: they cross the API boundary into merchant-facing copy.
REVIEW_NO_QUOTES = "no_shipping_quotes_available"
REVIEW_NO_MATCH = "no_shipping_method_matches_rule"
REVIEW_FELL_BACK = "shipping_rule_not_satisfied_fallback_used"
REVIEW_DESTINATION_UNKNOWN = "shipping_destination_unknown"


@dataclass(frozen=True, slots=True)
class ShippingQuote:
    """One supplier shipping option.

    ``cost`` is in the supplier's currency -- no conversion happens here, for
    the same reason ``landed_cost`` does none: burying a missing FX rate
    inside a shipping figure hides it.

    ``tracked`` is deliberately ``bool | None``. A supplier that does not say
    whether a service is tracked is *not* the same as one that says it is
    not, and a ``tracking_required`` rule must not accept an unknown.
    """

    service_name: str
    cost: Decimal | None
    delivery_days: int | None = None
    tracked: bool | None = None
    carrier: str | None = None

    def mentions(self, needle: str) -> bool:
        """Case-insensitive substring match over the service and carrier."""
        lowered = needle.strip().lower()
        if not lowered:
            return False
        return (
            lowered in (self.service_name or "").lower() or lowered in (self.carrier or "").lower()
        )


@dataclass(frozen=True, slots=True)
class ShippingSelection:
    """The chosen quote, or an explanation of why nothing was chosen."""

    quote: ShippingQuote | None
    rule: ShippingRule | None
    review_reasons: tuple[str, ...] = ()
    #: Set when `no_match_behaviour` is BLOCK_PUBLISH and nothing matched.
    blocks_publish: bool = False
    #: Human-readable, specific. "No tracked option under 12.00 within 20
    #: days" beats "no match" when a merchant has to fix it.
    explanation: str = ""

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    @property
    def cost(self) -> Decimal | None:
        return self.quote.cost if self.quote else None


def _eligible(quote: ShippingQuote, rule: ShippingRule) -> bool:
    """Whether a quote satisfies every hard constraint on the rule."""
    if quote.cost is None:
        # An unpriced quote cannot be reasoned about; it is not "free".
        return False
    for blocked in rule.blocked_carriers or []:
        if quote.mentions(blocked):
            return False
    preferred = [p for p in (rule.preferred_carriers or []) if p and p.strip()]
    if preferred and not any(quote.mentions(p) for p in preferred):
        return False
    if rule.tracking_required and quote.tracked is not True:
        # `is not True` on purpose: an unknown tracking status fails a
        # tracking requirement rather than being assumed compliant.
        return False
    if rule.max_shipping_cost is not None and quote.cost > rule.max_shipping_cost:
        return False
    if rule.max_delivery_days is not None:
        if quote.delivery_days is None or quote.delivery_days > rule.max_delivery_days:
            return False
    return True


def _pick(quotes: list[ShippingQuote], rule: ShippingRule) -> ShippingQuote | None:
    """Apply the selection strategy to already-eligible quotes."""
    if not quotes:
        return None
    strategy = rule.selection_strategy

    if strategy is ShippingSelectionStrategy.CHEAPEST:
        return min(quotes, key=lambda q: q.cost or Decimal("0"))

    if strategy is ShippingSelectionStrategy.CHEAPEST_TRACKED:
        tracked = [q for q in quotes if q.tracked is True]
        # No tracked option is not silently downgraded to an untracked one --
        # `tracking_required` is the switch for "must be tracked", and this
        # strategy without it means "prefer tracked, cheapest overall".
        pool = tracked or quotes
        return min(pool, key=lambda q: q.cost or Decimal("0"))

    known_speed = [q for q in quotes if q.delivery_days is not None]
    if strategy is ShippingSelectionStrategy.FASTEST:
        if not known_speed:
            return None
        return min(known_speed, key=lambda q: (q.delivery_days or 0, q.cost or Decimal("0")))

    # FASTEST_UNDER_COST
    ceiling = rule.max_shipping_cost
    affordable = [q for q in known_speed if ceiling is None or (q.cost or Decimal("0")) <= ceiling]
    if not affordable:
        return None
    return min(affordable, key=lambda q: (q.delivery_days or 0, q.cost or Decimal("0")))


def _describe(rule: ShippingRule) -> str:
    """Spell out the constraints, so a merchant can see what to relax."""
    parts: list[str] = []
    if rule.tracking_required:
        parts.append("tracked")
    if rule.max_shipping_cost is not None:
        parts.append(f"at or under {rule.max_shipping_cost}")
    if rule.max_delivery_days is not None:
        parts.append(f"delivered within {rule.max_delivery_days} days")
    preferred = [p for p in (rule.preferred_carriers or []) if p and p.strip()]
    if preferred:
        parts.append(f"from {', '.join(preferred)}")
    blocked = [b for b in (rule.blocked_carriers or []) if b and b.strip()]
    if blocked:
        parts.append(f"excluding {', '.join(blocked)}")
    return ", ".join(parts) if parts else "matching this rule"


def select_shipping(
    quotes: list[ShippingQuote],
    *,
    rule: ShippingRule | None,
    destination_country: str | None = None,
) -> ShippingSelection:
    """Choose a supplier shipping option, or refuse and say why.

    With no rule configured, the cheapest priced quote is returned and
    nothing is flagged -- that is the pre-M3A behaviour and a tenant that has
    not opted into shipping rules should not start seeing review flags.
    """
    priced = [q for q in quotes if q.cost is not None]

    if rule is None:
        if not priced:
            return ShippingSelection(
                quote=None,
                rule=None,
                review_reasons=(REVIEW_NO_QUOTES,),
                explanation="The supplier returned no priced shipping option.",
            )
        return ShippingSelection(
            quote=min(priced, key=lambda q: q.cost or Decimal("0")),
            rule=None,
            explanation="No shipping rule configured; cheapest supplier option used.",
        )

    country = rule.destination_country or destination_country
    if not country:
        # Never silently default to a country the merchant did not choose:
        # shipping cost and availability are entirely destination-dependent.
        return ShippingSelection(
            quote=None,
            rule=rule,
            review_reasons=(REVIEW_DESTINATION_UNKNOWN,),
            blocks_publish=rule.no_match_behaviour is ShippingNoMatchBehaviour.BLOCK_PUBLISH,
            explanation=(
                "No destination country is set on this rule or on the import, "
                "so shipping cost cannot be determined."
            ),
        )

    if not priced:
        return ShippingSelection(
            quote=None,
            rule=rule,
            review_reasons=(REVIEW_NO_QUOTES,),
            blocks_publish=rule.no_match_behaviour is ShippingNoMatchBehaviour.BLOCK_PUBLISH,
            explanation=f"The supplier returned no priced shipping option to {country}.",
        )

    eligible = [q for q in priced if _eligible(q, rule)]
    chosen = _pick(eligible, rule) if eligible else None
    if chosen is not None:
        return ShippingSelection(
            quote=chosen,
            rule=rule,
            explanation=f"{chosen.service_name} selected by {rule.selection_strategy.value}.",
        )

    detail = f"No shipping option to {country} is {_describe(rule)}."
    if rule.no_match_behaviour is ShippingNoMatchBehaviour.CHEAPEST_AVAILABLE:
        fallback = min(priced, key=lambda q: q.cost or Decimal("0"))
        return ShippingSelection(
            quote=fallback,
            rule=rule,
            # Still flagged. A fallback that satisfied nobody's constraint is
            # exactly the case a merchant must be told about.
            review_reasons=(REVIEW_FELL_BACK,),
            explanation=f"{detail} Fell back to {fallback.service_name}.",
        )

    return ShippingSelection(
        quote=None,
        rule=rule,
        review_reasons=(REVIEW_NO_MATCH,),
        blocks_publish=rule.no_match_behaviour is ShippingNoMatchBehaviour.BLOCK_PUBLISH,
        explanation=detail,
    )


__all__ = [
    "REVIEW_DESTINATION_UNKNOWN",
    "REVIEW_FELL_BACK",
    "REVIEW_NO_MATCH",
    "REVIEW_NO_QUOTES",
    "ShippingQuote",
    "ShippingSelection",
    "select_shipping",
]
