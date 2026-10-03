"""Dynamic pricing rules and audit trail.

Sell price lives on the product; rules decide how it is derived from cost.
Every applied change writes a ``PriceChange`` so "why is this £19.99" is
answerable without archaeology.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase

_MONEY = Numeric(16, 4)


class PricingScope(StrEnum):
    """Where a rule applies. Narrower scopes win when several match.

    Ordered narrowest-last on purpose; :func:`app.services.pricing_engine.
    select_rule` reads that order rather than hard-coding a ranking in two
    places. M3A adds ``VARIANT`` at the narrow end so a single SKU can be
    overridden without splitting the product out of its store rule.
    """

    GLOBAL = "global"
    STORE = "store"
    CATEGORY = "category"
    PRODUCT = "product"
    VARIANT = "variant"


class PricingStrategy(StrEnum):
    """How the sell price is computed from landed cost.

    ``PERCENTAGE_MARKUP`` and ``TARGET_MARGIN`` are the two merchants most
    often confuse, and the confusion is expensive: at 50% a 10.00 cost
    becomes 15.00 as markup but 20.00 as margin. They are separate members
    rather than one "percentage" strategy with a flag, so a stored rule can
    never be ambiguous about which arithmetic produced its prices.
    """

    PERCENTAGE_MARKUP = "percentage_markup"
    FIXED_MARKUP = "fixed_markup"
    TIERED = "tiered"
    #: price = landed / (1 - margin). Profit as a share of the *selling price*.
    TARGET_MARGIN = "target_margin"
    #: price = landed * (1 + markup) + fixed. Both components together.
    HYBRID = "hybrid"


class PriceRounding(StrEnum):
    """Charm-pricing applied as the final step, after every other rule.

    Last on purpose: rounding before the floor/ceiling checks could push a
    price back under ``min_profit`` or over ``max_price``, silently breaking
    the guarantee those fields exist to make.
    """

    NONE = "none"
    NINETY_NINE = "ninety_nine"
    NINETY_FIVE = "ninety_five"
    WHOLE = "whole"


class ShippingCostHandling(StrEnum):
    """What happens to the supplier's shipping charge.

    This is a *pricing* decision, not a carrier decision, which is why it
    lives beside the pricing rule rather than in the shipping rule: it
    changes the landed cost the strategy is applied to.
    """

    #: Supplier shipping is part of landed cost, so the buyer pays it inside
    #: the item price. The default, and what "free shipping" listings mean.
    INCLUDE_IN_PRICE = "include_in_price"
    #: Buyer pays shipping as a separate line. Landed cost excludes it, and
    #: the figure is surfaced separately so the merchant can charge it.
    CHARGE_SEPARATELY = "charge_separately"
    #: Merchant eats it. Landed cost includes it (so profit is honest) but
    #: the price is computed as if it did not exist.
    ABSORB_FROM_PROFIT = "absorb_from_profit"


class PricingRule(TenantScopedBase):
    """One pricing rule.

    ``tiers`` holds an ordered list of ``{min_cost, max_cost, markup_percent}``
    objects when strategy is ``tiered``; otherwise it is empty.
    """

    __tablename__ = "pricing_rules"

    __table_args__ = (
        Index("ix_pricing_rules_tenant_scope", "tenant_id", "scope", "priority"),
        UniqueConstraint("tenant_id", "name", name="uq_pricing_rules_tenant_name"),
        # FK target for `global_rule_versions`; see migration 0024.
        UniqueConstraint("tenant_id", "id", name="uq_pricing_rules_tenant_id_id"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[PricingScope] = mapped_column(
        Enum(
            PricingScope,
            name="pricing_scope",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=PricingScope.GLOBAL,
    )
    strategy: Mapped[PricingStrategy] = mapped_column(
        Enum(
            PricingStrategy,
            name="pricing_strategy",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=PricingStrategy.PERCENTAGE_MARKUP,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: Higher wins when two rules share a scope level.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    markup_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    markup_fixed: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    #: Target gross margin as a percentage of the *selling* price. Validated
    #: below 100 at the schema layer: at exactly 100 the formula divides by
    #: zero, and above it the price would be negative.
    margin_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: Profit floor measured against **landed** cost, so it means true profit
    #: after supplier shipping rather than the item-only figure it meant
    #: before M3A.
    min_profit: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    #: Per-variant profit floor. Separate from ``min_profit`` because a
    #: product-level floor can be satisfied on average while an individual
    #: SKU still sells at a loss.
    min_profit_per_variant: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    #: Known duty/import tax as a percentage of item + shipping. Merchant
    #: knowledge, not supplier data -- the supplier never quotes this, and
    #: presenting it as though it came from them would be an invention.
    duty_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    #: Flat known fees per unit (payment processing, inspection, packaging).
    fees_fixed: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    #: Track E2 (M24C): fees charged as a share of the *selling* price —
    #: marketplace final-value fees, payment processing. Unlike the fees
    #: above they grow with the price, so the price is grossed up to keep
    #: the markup, margin and profit floor true after the fee is taken.
    sale_fee_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    #: Absolute price floor, independent of cost.
    min_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    max_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    rounding: Mapped[PriceRounding] = mapped_column(
        Enum(
            PriceRounding,
            name="price_rounding",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=PriceRounding.NONE,
        server_default=PriceRounding.NONE.value,
    )
    #: Compare-at ("was") price as a percentage above the selling price.
    #: Display only -- never a floor, never part of profit.
    compare_at_percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    shipping_cost_handling: Mapped[ShippingCostHandling] = mapped_column(
        Enum(
            ShippingCostHandling,
            name="shipping_cost_handling",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ShippingCostHandling.INCLUDE_IN_PRICE,
        server_default=ShippingCostHandling.INCLUDE_IN_PRICE.value,
    )
    #: Applies to products imported *after* this rule is saved. Existing rows
    #: are never touched by a settings save -- that needs the explicit
    #: preview-and-confirm path (M3A section 8).
    applies_to_new_imports: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    #: Bumped on every material change. Stamped onto each `PriceChange` so a
    #: historical price can be traced to the arithmetic that produced it.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    #: Ordered list of ``{minCost, maxCost, markupPercent}`` for tiered rules.
    tiers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PriceChange(TenantScopedBase):
    """Audit row for one sell-price update."""

    __tablename__ = "price_changes"

    __table_args__ = (
        Index("ix_price_changes_tenant_product", "tenant_id", "product_id"),
        Index("ix_price_changes_tenant_created", "tenant_id", "created_at"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("pricing_rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
    )
    previous_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    new_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    cost_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    #: The landed cost the price was actually derived from (item + supplier
    #: shipping + known fees). Stored alongside ``cost_price`` rather than
    #: replacing it, so pre-M3A rows stay interpretable.
    landed_cost: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    #: Which version of ``rule_id`` produced this figure. Without it, a rule
    #: edited afterwards makes every historical price unexplainable.
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Groups every row written by one confirmed bulk application, so a
    #: repeat of the same confirmed operation is detectable as a repeat.
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="rule_apply")
    applied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShippingSelectionStrategy(StrEnum):
    """How one supplier shipping quote is chosen from the available list."""

    CHEAPEST = "cheapest"
    CHEAPEST_TRACKED = "cheapest_tracked"
    FASTEST = "fastest"
    #: Fastest option whose cost is at or under ``max_shipping_cost``.
    FASTEST_UNDER_COST = "fastest_under_cost"


class ShippingNoMatchBehaviour(StrEnum):
    """What happens when no quote satisfies the rule.

    There is deliberately no "pick something anyway" member. Every option
    stops short of committing to a shipment the merchant did not agree to;
    they differ only in how loudly they stop.
    """

    #: Hold the product out of automatic publishing and flag it.
    NEEDS_REVIEW = "needs_review"
    #: As above, and additionally refuse the publish call outright.
    BLOCK_PUBLISH = "block_publish"
    #: Fall back to the cheapest available quote -- still flagged, so the
    #: merchant sees that their constraint was not met.
    CHEAPEST_AVAILABLE = "cheapest_available"


class ShippingRule(TenantScopedBase):
    """Supplier-shipping preferences for a tenant, store, product or variant.

    **This governs which supplier shipping option we buy and what it costs
    us**, not what a customer is charged at a Shopify checkout. M3A does not
    create or modify Shopify delivery profiles: the existing integration has
    no safe path for it, and adding one here would let a settings save change
    live checkout behaviour.
    """

    __tablename__ = "shipping_rules"

    __table_args__ = (
        Index("ix_shipping_rules_tenant_scope", "tenant_id", "scope", "priority"),
        UniqueConstraint("tenant_id", "name", name="uq_shipping_rules_tenant_name"),
        # FK target for `global_rule_versions`; see migration 0024.
        UniqueConstraint("tenant_id", "id", name="uq_shipping_rules_tenant_id_id"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[PricingScope] = mapped_column(
        Enum(
            PricingScope,
            name="pricing_scope",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=PricingScope.GLOBAL,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    #: ISO 3166-1 alpha-2. Null means "whatever the import supplied"; it is
    #: never silently defaulted to a country the merchant did not choose.
    destination_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    selection_strategy: Mapped[ShippingSelectionStrategy] = mapped_column(
        Enum(
            ShippingSelectionStrategy,
            name="shipping_selection_strategy",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ShippingSelectionStrategy.CHEAPEST_TRACKED,
        server_default=ShippingSelectionStrategy.CHEAPEST_TRACKED.value,
    )
    max_delivery_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_shipping_cost: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    tracking_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Carrier/service names, matched case-insensitively as substrings of the
    #: quote's service name. An empty list means "no preference", never
    #: "nothing allowed" -- the difference matters when a rule is half-filled.
    preferred_carriers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    blocked_carriers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    no_match_behaviour: Mapped[ShippingNoMatchBehaviour] = mapped_column(
        Enum(
            ShippingNoMatchBehaviour,
            name="shipping_no_match_behaviour",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ShippingNoMatchBehaviour.NEEDS_REVIEW,
        server_default=ShippingNoMatchBehaviour.NEEDS_REVIEW.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class GlobalRuleKind(StrEnum):
    """Which rule table a history row describes."""

    PRICING = "pricing"
    SHIPPING = "shipping"


class GlobalRuleVersion(TenantScopedBase):
    """One immutable entry in a rule's history.

    Append-only by construction: nothing in the service layer updates or
    deletes these rows, and the rule itself carries only its *current*
    version number. "Who changed what, when, and what was it before" has to
    survive the rule being edited again -- and being deleted.
    """

    __tablename__ = "global_rule_versions"

    __table_args__ = (
        Index("ix_global_rule_versions_tenant_rule", "tenant_id", "rule_kind", "rule_id"),
        Index("ix_global_rule_versions_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_global_rule_versions_tenant_kind_created",
            "tenant_id",
            "rule_kind",
            "created_at",
        ),
        UniqueConstraint(
            "tenant_id", "rule_kind", "rule_id", "version", name="uq_global_rule_version"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pricing_rule_id"],
            ["pricing_rules.tenant_id", "pricing_rules.id"],
            name="fk_global_rule_versions_pricing_rule",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "shipping_rule_id"],
            ["shipping_rules.tenant_id", "shipping_rules.id"],
            name="fk_global_rule_versions_shipping_rule",
            ondelete="RESTRICT",
        ),
    )

    rule_kind: Mapped[GlobalRuleKind] = mapped_column(
        Enum(
            GlobalRuleKind,
            name="global_rule_kind",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    #: The rule this entry describes. ``rule_kind`` selects which table it
    #: refers to, which is why it carries no foreign key of its own -- one
    #: column cannot reference two tables. The typed columns below carry the
    #: actual referential integrity (M3A-2, migration 0024); this stays
    #: because every query reads it and a join through two nullable columns
    #: would be worse to read than a denormalised key a CHECK keeps honest.
    rule_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    #: Exactly one of these is set, matching ``rule_kind``, and each carries a
    #: **composite** foreign key on ``(tenant_id, rule_id)``. The composite is
    #: the point: a single-column reference would let a version row in one
    #: tenant point at a rule in another, with only application code in the
    #: way. Rules are soft-deleted, so these never block history from
    #: outliving a deleted rule -- the row it points at is still there.
    pricing_rule_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    shipping_rule_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: Frozen copy of the rule as it stood at this version. The previous/new
    #: value pairs describe the *delta*; this answers "what were all the
    #: settings when this price was calculated" without replaying history.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Field names that differ between ``previous_values`` and ``new_values``.
    changed_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    previous_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    new_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Whether the rule was active as of this version.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    #: Only non-zero when the merchant explicitly applied this version to
    #: existing products. Saving a rule records 0, which is the honest
    #: number: a save changes nothing by itself.
    products_affected: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


__all__ = [
    "GlobalRuleKind",
    "GlobalRuleVersion",
    "PriceChange",
    "PriceRounding",
    "PricingRule",
    "PricingScope",
    "PricingStrategy",
    "ShippingCostHandling",
    "ShippingNoMatchBehaviour",
    "ShippingRule",
    "ShippingSelectionStrategy",
]
