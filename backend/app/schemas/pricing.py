"""Pricing engine API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.models.pricing import PricingScope, PricingStrategy
from app.schemas.base import CamelCaseModel


class PricingTierItem(CamelCaseModel):
    min_cost: Decimal
    max_cost: Decimal | None = None
    markup_percent: Decimal


class PricingRuleCreate(CamelCaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: PricingScope = PricingScope.GLOBAL
    strategy: PricingStrategy = PricingStrategy.PERCENTAGE_MARKUP
    store_id: uuid.UUID | None = None
    category_id: str | None = None
    product_id: uuid.UUID | None = None
    priority: int = Field(default=100, ge=0, le=10_000)
    markup_percent: Decimal | None = None
    markup_fixed: Decimal | None = None
    min_profit: Decimal | None = None
    max_price: Decimal | None = None
    tiers: list[dict[str, Any]] = Field(default_factory=list)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    is_active: bool = True


class PricingRuleUpdate(CamelCaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    priority: int | None = Field(default=None, ge=0, le=10_000)
    markup_percent: Decimal | None = None
    markup_fixed: Decimal | None = None
    min_profit: Decimal | None = None
    max_price: Decimal | None = None
    tiers: list[dict[str, Any]] | None = None
    is_active: bool | None = None


class PricingRuleRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    scope: PricingScope
    strategy: PricingStrategy
    store_id: uuid.UUID | None
    category_id: str | None
    product_id: uuid.UUID | None
    priority: int
    markup_percent: Decimal | None
    markup_fixed: Decimal | None
    min_profit: Decimal | None
    max_price: Decimal | None
    tiers: list[dict[str, Any]]
    currency: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PricePreviewRequest(CamelCaseModel):
    product_ids: list[uuid.UUID] | None = None
    store_id: uuid.UUID | None = None
    limit: int = Field(default=100, ge=1, le=500)


class PricePreviewItem(CamelCaseModel):
    product_id: uuid.UUID
    title: str
    cost_price: Decimal | None
    current_sell_price: Decimal | None
    proposed_sell_price: Decimal | None
    rule_id: uuid.UUID | None
    rule_name: str | None
    currency: str | None


class PricePreviewResponse(CamelCaseModel):
    items: list[PricePreviewItem]
    would_change: int


class PricingApplyRequest(CamelCaseModel):
    product_ids: list[uuid.UUID] | None = None
    store_id: uuid.UUID | None = None
    limit: int = Field(default=500, ge=1, le=2000)


class PriceChangeRead(CamelCaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    rule_id: uuid.UUID | None
    store_id: uuid.UUID | None
    previous_price: Decimal | None
    new_price: Decimal | None
    cost_price: Decimal | None
    currency: str | None
    reason: str
    applied_at: datetime
    created_at: datetime
