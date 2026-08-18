"""Which rule governs this product, and why (M3A).

Split out of ``pricing_engine`` deliberately. Resolution answers "which rule
applies"; the engine answers "what price does it produce". Keeping them apart
is what lets the preview, the import path and the bulk apply all agree on the
governing rule without any of them re-deriving it, and it makes the precedence
order testable without touching arithmetic.

**Nothing here reads product titles, option names or description text.** A
rule applies because it was configured against an id, never because a word
appeared in a listing. Inferring an override from text is how a merchant ends
up with a "Large" variant priced by a rule they never wrote.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.models.pricing import PricingRule, PricingScope, ShippingRule

#: Narrowest wins. One ordering, read by both resolvers, so pricing and
#: shipping precedence can never drift apart.
SCOPE_RANK: dict[PricingScope, int] = {
    PricingScope.VARIANT: 5,
    PricingScope.PRODUCT: 4,
    PricingScope.CATEGORY: 3,
    PricingScope.STORE: 2,
    PricingScope.GLOBAL: 1,
}

#: Merchant-facing explanation of why a scope won, keyed by scope. Kept beside
#: the ranking so a new scope cannot be added without someone writing the
#: sentence a merchant will read.
SCOPE_REASON: dict[PricingScope, str] = {
    PricingScope.VARIANT: "A variant-level override applies to this SKU.",
    PricingScope.PRODUCT: "A product-level override applies to this product.",
    PricingScope.CATEGORY: "A category rule applies to this product's category.",
    PricingScope.STORE: "A store rule applies to this product's store.",
    PricingScope.GLOBAL: "The global tenant rule applies; no narrower override exists.",
}


class _ScopedRule(Protocol):
    """The shape both rule tables share for resolution purposes."""

    scope: PricingScope
    priority: int
    store_id: uuid.UUID | None
    category_id: str | None
    product_id: uuid.UUID | None
    variant_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class RuleResolution[RuleT]:
    """The governing rule plus everything needed to explain the choice.

    ``overridden`` lists the rules that matched but lost, narrowest first.
    The UI renders it as the inheritance chain -- a merchant who cannot see
    what a rule overrode cannot tell whether their global rule is doing
    anything at all.
    """

    rule: RuleT | None
    scope: PricingScope | None
    version: int | None
    reason: str
    overridden: tuple[RuleT, ...] = ()

    @property
    def found(self) -> bool:
        return self.rule is not None


def _matches(
    rule: _ScopedRule,
    *,
    variant_id: uuid.UUID | None,
    product_id: uuid.UUID | None,
    store_id: uuid.UUID | None,
    category_id: str | None,
) -> bool:
    """Whether a rule's configured scope covers this product/variant.

    Every branch compares identifiers. There is deliberately no text match.
    """
    if rule.scope is PricingScope.VARIANT:
        return variant_id is not None and rule.variant_id == variant_id
    if rule.scope is PricingScope.PRODUCT:
        return product_id is not None and rule.product_id == product_id
    if rule.scope is PricingScope.CATEGORY:
        return category_id is not None and rule.category_id == category_id
    if rule.scope is PricingScope.STORE:
        return store_id is not None and rule.store_id == store_id
    return rule.scope is PricingScope.GLOBAL


def _resolve[RuleT: _ScopedRule](
    candidates: list[RuleT],
    *,
    variant_id: uuid.UUID | None,
    product_id: uuid.UUID | None,
    store_id: uuid.UUID | None,
    category_id: str | None,
    empty_reason: str,
) -> RuleResolution[RuleT]:
    matching = [
        rule
        for rule in candidates
        if _matches(
            rule,
            variant_id=variant_id,
            product_id=product_id,
            store_id=store_id,
            category_id=category_id,
        )
    ]
    if not matching:
        return RuleResolution(rule=None, scope=None, version=None, reason=empty_reason)

    matching.sort(key=lambda r: (SCOPE_RANK[r.scope], r.priority), reverse=True)
    winner = matching[0]
    reason = SCOPE_REASON[winner.scope]
    if len(matching) > 1 and SCOPE_RANK[matching[1].scope] == SCOPE_RANK[winner.scope]:
        # Same scope, so priority decided it. Say so rather than claiming the
        # scope was the reason -- two global rules is a common misconfiguration
        # and the merchant needs to see which one is live.
        reason = f"{reason} Priority {winner.priority} beat a rule at the same level."
    return RuleResolution(
        rule=winner,
        scope=winner.scope,
        version=getattr(winner, "version", None),
        reason=reason,
        overridden=tuple(matching[1:]),
    )


def resolve_pricing_rule(
    candidates: list[PricingRule],
    *,
    product_id: uuid.UUID | None,
    variant_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    category_id: str | None = None,
) -> RuleResolution[PricingRule]:
    """The pricing rule that governs this product or variant.

    A ``None`` result is a normal state, not an error: the product simply
    keeps whatever price it already has.
    """
    return _resolve(
        candidates,
        variant_id=variant_id,
        product_id=product_id,
        store_id=store_id,
        category_id=category_id,
        empty_reason="No pricing rule applies; this product keeps its current price.",
    )


def resolve_shipping_rule(
    candidates: list[ShippingRule],
    *,
    product_id: uuid.UUID | None,
    variant_id: uuid.UUID | None = None,
    store_id: uuid.UUID | None = None,
    category_id: str | None = None,
) -> RuleResolution[ShippingRule]:
    """The shipping rule that governs supplier-shipping selection here."""
    return _resolve(
        candidates,
        variant_id=variant_id,
        product_id=product_id,
        store_id=store_id,
        category_id=category_id,
        empty_reason="No shipping rule applies; the supplier default is used.",
    )


__all__ = [
    "SCOPE_RANK",
    "SCOPE_REASON",
    "RuleResolution",
    "resolve_pricing_rule",
    "resolve_shipping_rule",
]
