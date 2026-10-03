"""Request/response contracts for global pricing and shipping rules (M3A-2).

Validation lives here rather than in the service so an impossible rule is
rejected at the boundary with the platform's standard error envelope, before
any of it reaches a repository.

Two constraints are worth calling out because they are easy to get wrong:
``margin_percent`` must stay **below** 100 (at exactly 100 the formula
divides by zero and above it the price is negative), and ``min_price`` must
not exceed ``max_price`` (a rule that cannot be satisfied should fail at
save time, not silently every time it prices something).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from app.models.pricing import (
    GlobalRuleKind,
    PriceRounding,
    PricingScope,
    PricingStrategy,
    ShippingCostHandling,
    ShippingNoMatchBehaviour,
    ShippingSelectionStrategy,
)
from app.schemas.base import CamelCaseModel

_MONEY = Field(default=None, ge=0, decimal_places=4)
_PERCENT = Field(default=None, ge=0, le=1000, decimal_places=4)

#: Scopes that require an identifier, and which one. Keeping the mapping in
#: one place means a new scope cannot be added without deciding what
#: identifies it -- the alternative is a rule that matches nothing and gives
#: no clue why.
_SCOPE_REQUIREMENTS: dict[PricingScope, str] = {
    PricingScope.STORE: "store_id",
    PricingScope.CATEGORY: "category_id",
    PricingScope.PRODUCT: "product_id",
    PricingScope.VARIANT: "variant_id",
}


class _ScopedRuleBase(CamelCaseModel):
    """Shared scope validation for both rule kinds."""

    @model_validator(mode="after")
    def _scope_needs_its_identifier(self) -> Self:
        scope = getattr(self, "scope", None)
        if scope is None:
            return self
        required = _SCOPE_REQUIREMENTS.get(scope)
        if required is not None and getattr(self, required, None) is None:
            raise ValueError(f"A {scope.value} rule requires {required}.")
        if scope is PricingScope.GLOBAL:
            for field in _SCOPE_REQUIREMENTS.values():
                if getattr(self, field, None) is not None:
                    raise ValueError(f"A global rule must not set {field}.")
        return self


class PricingRuleWrite(_ScopedRuleBase):
    """Fields a merchant may set on a pricing rule."""

    name: str = Field(min_length=1, max_length=128)
    scope: PricingScope = PricingScope.GLOBAL
    strategy: PricingStrategy = PricingStrategy.PERCENTAGE_MARKUP
    priority: int = Field(default=100, ge=0, le=10_000)

    store_id: uuid.UUID | None = None
    category_id: str | None = Field(default=None, max_length=64)
    product_id: uuid.UUID | None = None
    variant_id: uuid.UUID | None = None

    markup_percent: Decimal | None = _PERCENT
    markup_fixed: Decimal | None = _MONEY
    #: Strictly below 100: the target-margin formula divides by (1 - m/100).
    margin_percent: Decimal | None = Field(default=None, ge=0, lt=100, decimal_places=4)

    min_profit: Decimal | None = _MONEY
    min_profit_per_variant: Decimal | None = _MONEY
    min_price: Decimal | None = _MONEY
    max_price: Decimal | None = _MONEY

    duty_percent: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=4)
    fees_fixed: Decimal | None = _MONEY
    #: Track E2: marketplace/payment fee as a share of the selling price.
    sale_fee_percent: Decimal | None = Field(default=None, ge=0, lt=100, decimal_places=4)

    rounding: PriceRounding = PriceRounding.NONE
    compare_at_percent: Decimal | None = _PERCENT
    shipping_cost_handling: ShippingCostHandling = ShippingCostHandling.INCLUDE_IN_PRICE
    applies_to_new_imports: bool = True
    tiers: list[dict[str, Any]] = Field(default_factory=list)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool = True

    @field_validator("currency")
    @classmethod
    def _currency_is_uppercase_alpha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if not code.isalpha():
            raise ValueError("Currency must be a 3-letter ISO 4217 code.")
        return code

    @model_validator(mode="after")
    def _strategy_has_its_inputs(self) -> Self:
        if self.strategy is PricingStrategy.PERCENTAGE_MARKUP and self.markup_percent is None:
            raise ValueError("A markup-percentage rule requires markupPercent.")
        if self.strategy is PricingStrategy.FIXED_MARKUP and self.markup_fixed is None:
            raise ValueError("A fixed-profit rule requires markupFixed.")
        if self.strategy is PricingStrategy.TARGET_MARGIN and self.margin_percent is None:
            raise ValueError("A target-margin rule requires marginPercent.")
        if self.strategy is PricingStrategy.HYBRID and (
            self.markup_percent is None and self.markup_fixed is None
        ):
            raise ValueError("A hybrid rule requires markupPercent or markupFixed.")
        if self.strategy is PricingStrategy.TIERED and not self.tiers:
            raise ValueError("A tiered rule requires at least one tier.")
        return self

    @model_validator(mode="after")
    def _floors_and_ceilings_are_satisfiable(self) -> Self:
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("minPrice cannot exceed maxPrice.")
        return self


class PricingRuleCreateRequest(PricingRuleWrite):
    """Optional free-text note recorded against the created version."""

    note: str | None = Field(default=None, max_length=500)


class PricingRuleUpdateRequest(PricingRuleWrite):
    #: Mandatory. A rule is edited from a settings screen that may sit open
    #: for a long time; without the token a stale save silently wins.
    expected_updated_at: datetime
    note: str | None = Field(default=None, max_length=500)


class ShippingRuleWrite(_ScopedRuleBase):
    name: str = Field(min_length=1, max_length=128)
    scope: PricingScope = PricingScope.GLOBAL
    priority: int = Field(default=100, ge=0, le=10_000)

    store_id: uuid.UUID | None = None
    category_id: str | None = Field(default=None, max_length=64)
    product_id: uuid.UUID | None = None
    variant_id: uuid.UUID | None = None

    destination_country: str | None = Field(default=None, min_length=2, max_length=2)
    selection_strategy: ShippingSelectionStrategy = ShippingSelectionStrategy.CHEAPEST_TRACKED
    max_delivery_days: int | None = Field(default=None, ge=1, le=365)
    max_shipping_cost: Decimal | None = _MONEY
    tracking_required: bool = False
    preferred_carriers: list[str] = Field(default_factory=list)
    blocked_carriers: list[str] = Field(default_factory=list)
    no_match_behaviour: ShippingNoMatchBehaviour = ShippingNoMatchBehaviour.NEEDS_REVIEW
    is_active: bool = True

    @field_validator("destination_country")
    @classmethod
    def _country_is_uppercase_alpha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if not code.isalpha():
            raise ValueError("Destination country must be a 2-letter ISO 3166-1 code.")
        return code

    @field_validator("preferred_carriers", "blocked_carriers")
    @classmethod
    def _carriers_are_non_empty_strings(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if len(cleaned) > 50:
            raise ValueError("At most 50 carriers may be listed.")
        return cleaned

    @model_validator(mode="after")
    def _carrier_lists_do_not_contradict(self) -> Self:
        """A carrier that is both preferred and blocked makes the rule
        unsatisfiable in a way that is invisible until nothing ships."""
        overlap = {c.lower() for c in self.preferred_carriers} & {
            c.lower() for c in self.blocked_carriers
        }
        if overlap:
            raise ValueError(
                f"A carrier cannot be both preferred and blocked: {', '.join(sorted(overlap))}."
            )
        return self

    @model_validator(mode="after")
    def _strategy_has_what_it_needs(self) -> Self:
        if (
            self.selection_strategy is ShippingSelectionStrategy.FASTEST_UNDER_COST
            and self.max_shipping_cost is None
        ):
            raise ValueError(
                "A fastest-under-cost rule requires maxShippingCost; without a ceiling "
                "it is just fastest."
            )
        return self


class ShippingRuleCreateRequest(ShippingRuleWrite):
    note: str | None = Field(default=None, max_length=500)


class ShippingRuleUpdateRequest(ShippingRuleWrite):
    expected_updated_at: datetime
    note: str | None = Field(default=None, max_length=500)


class RuleActivationRequest(CamelCaseModel):
    """Activate or deactivate. Idempotent: re-sending the current state is a
    no-op that writes nothing and adds no history entry."""

    is_active: bool
    expected_updated_at: datetime
    note: str | None = Field(default=None, max_length=500)


class PricingRuleRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    scope: PricingScope
    strategy: PricingStrategy
    priority: int
    store_id: uuid.UUID | None
    category_id: str | None
    product_id: uuid.UUID | None
    variant_id: uuid.UUID | None
    markup_percent: Decimal | None
    markup_fixed: Decimal | None
    margin_percent: Decimal | None
    min_profit: Decimal | None
    min_profit_per_variant: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    duty_percent: Decimal | None
    fees_fixed: Decimal | None
    sale_fee_percent: Decimal | None
    rounding: PriceRounding
    compare_at_percent: Decimal | None
    shipping_cost_handling: ShippingCostHandling
    applies_to_new_imports: bool
    tiers: list[dict[str, Any]]
    currency: str | None
    is_active: bool
    version: int
    updated_at: datetime


class ShippingRuleRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    scope: PricingScope
    priority: int
    store_id: uuid.UUID | None
    category_id: str | None
    product_id: uuid.UUID | None
    variant_id: uuid.UUID | None
    destination_country: str | None
    selection_strategy: ShippingSelectionStrategy
    max_delivery_days: int | None
    max_shipping_cost: Decimal | None
    tracking_required: bool
    preferred_carriers: list[str]
    blocked_carriers: list[str]
    no_match_behaviour: ShippingNoMatchBehaviour
    is_active: bool
    version: int
    updated_at: datetime


class RuleVersionRead(CamelCaseModel):
    id: uuid.UUID
    rule_kind: GlobalRuleKind
    rule_id: uuid.UUID
    version: int
    changed_fields: list[str]
    previous_values: dict[str, Any]
    new_values: dict[str, Any]
    snapshot: dict[str, Any]
    changed_by_user_id: uuid.UUID | None
    note: str | None
    is_active: bool
    products_affected: int
    created_at: datetime


class RuleResolutionRead(CamelCaseModel):
    """Why this rule governs, in a shape the UI can render as inheritance."""

    rule_id: uuid.UUID | None
    rule_name: str | None
    scope: PricingScope | None
    version: int | None
    reason: str
    overridden_rule_ids: list[uuid.UUID]


class PreviewRequest(CamelCaseModel):
    """Sample figures for a live preview. Nothing here is persisted."""

    item_cost: Decimal | None = Field(default=None, ge=0, decimal_places=4)
    shipping_cost: Decimal | None = Field(default=None, ge=0, decimal_places=4)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    rule_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    variant_id: uuid.UUID | None = None
    store_id: uuid.UUID | None = None
    category_id: str | None = Field(default=None, max_length=64)


class PreviewResponse(CamelCaseModel):
    """Every figure the merchant is shown, and why."""

    resolution: RuleResolutionRead
    item_cost: Decimal
    shipping_cost: Decimal
    fees: Decimal
    landed_cost: Decimal
    profit_basis: Decimal
    separate_shipping_charge: Decimal | None
    #: What the strategy produced before the rounding mode moved it. Shown so
    #: a rule that computes 20.00 and sells at 19.99 reads as the charm
    #: rounding the merchant chose rather than an arithmetic error.
    price_before_rounding: Decimal | None
    proposed_price: Decimal | None
    compare_at_price: Decimal | None
    profit: Decimal | None
    #: Profit as a share of cost.
    markup_percent: Decimal | None
    #: Profit as a share of the selling price. Always lower than markup.
    margin_percent: Decimal | None
    rounding: PriceRounding
    needs_review: bool
    review_reasons: list[str]


__all__ = [
    "PreviewRequest",
    "PreviewResponse",
    "PricingRuleCreateRequest",
    "PricingRuleRead",
    "PricingRuleUpdateRequest",
    "RuleActivationRequest",
    "RuleResolutionRead",
    "RuleVersionRead",
    "ShippingRuleCreateRequest",
    "ShippingRuleRead",
    "ShippingRuleUpdateRequest",
]
