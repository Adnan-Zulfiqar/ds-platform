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
    """Where a rule applies. Narrower scopes win when several match."""

    GLOBAL = "global"
    STORE = "store"
    CATEGORY = "category"
    PRODUCT = "product"


class PricingStrategy(StrEnum):
    """How the sell price is computed from cost."""

    PERCENTAGE_MARKUP = "percentage_markup"
    FIXED_MARKUP = "fixed_markup"
    TIERED = "tiered"


class PricingRule(TenantScopedBase):
    """One pricing rule.

    ``tiers`` holds an ordered list of ``{min_cost, max_cost, markup_percent}``
    objects when strategy is ``tiered``; otherwise it is empty.
    """

    __tablename__ = "pricing_rules"

    __table_args__ = (
        Index("ix_pricing_rules_tenant_scope", "tenant_id", "scope", "priority"),
        UniqueConstraint("tenant_id", "name", name="uq_pricing_rules_tenant_name"),
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
    min_profit: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    max_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
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
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False, default="rule_apply")
    applied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "PriceChange",
    "PricingRule",
    "PricingScope",
    "PricingStrategy",
]
