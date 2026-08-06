"""Draft pricing workspace schemas (Stage 5).

Authoritative profit/margin math lives on the server as Decimal strings on the
wire — the browser displays only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from app.schemas.base import CamelCaseModel


class DraftPricingApplyMode(StrEnum):
    PERCENTAGE_MARKUP = "percentage_markup"
    FIXED_MARKUP = "fixed_markup"
    SET_SELL_PRICE = "set_sell_price"
    SET_COMPARE_AT = "set_compare_at"


class DraftVariantPricingRow(CamelCaseModel):
    variant_id: uuid.UUID
    label: str | None = None
    is_enabled: bool
    supplier_cost: Decimal | None = None
    supplier_currency: str | None = None
    converted_cost: Decimal | None = None
    conversion_rate_timestamp: datetime | None = None
    supplier_shipping_cost: Decimal | None = None
    shipping_cost_available: bool = False
    handling_cost: Decimal
    fee_estimate: Decimal
    sell_price: Decimal | None = None
    compare_at_price: Decimal | None = None
    proposed_sell_price: Decimal | None = None
    profit: Decimal | None = None
    margin_percent: Decimal | None = None
    break_even_price: Decimal | None = None
    pricing_rule_source: str | None = None
    manual_override: bool = False


class DraftPricingWorkspaceRead(CamelCaseModel):
    product_id: uuid.UUID
    currency: str | None = None
    product_sell_price: Decimal | None = None
    cost_price_min: Decimal | None = None
    cost_price_max: Decimal | None = None
    shipping_cost: Decimal | None = None
    shipping_cost_available: bool = False
    shipping_warning: str | None = None
    fx_note: str
    variants: list[DraftVariantPricingRow] = Field(default_factory=list)


class DraftPricingApplyRequest(CamelCaseModel):
    mode: DraftPricingApplyMode
    markup_percent: Decimal | None = None
    markup_fixed: Decimal | None = None
    sell_price: Decimal | None = None
    compare_at_price: Decimal | None = None
    variant_ids: list[uuid.UUID] | None = None
    round_to_cents: bool = True
    handling_cost: Decimal = Field(default=Decimal("0"))
    fee_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
