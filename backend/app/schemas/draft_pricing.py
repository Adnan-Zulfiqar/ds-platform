"""Draft pricing workspace schemas.

Authoritative profit/margin math lives on the server as Decimal strings on the
wire — the browser displays only. Cross-currency identity conversion is
forbidden; when FX is required and unavailable, ``pricing_blocked`` is true and
calculated profit fields are null.
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
    TARGET_MARGIN = "target_margin"
    SET_SELL_PRICE = "set_sell_price"
    SET_COMPARE_AT = "set_compare_at"


class DraftVariantPricingRow(CamelCaseModel):
    variant_id: uuid.UUID
    label: str | None = None
    is_enabled: bool
    supplier_cost: Decimal | None = None
    supplier_currency: str | None = None
    converted_cost: Decimal | None = None
    converted_currency: str | None = None
    conversion_required: bool = False
    conversion_type: str | None = None  # direct | fx | unavailable
    conversion_rate: Decimal | None = None
    conversion_rate_timestamp: datetime | None = None
    fx_fetched_at: datetime | None = None
    fx_provider: str | None = None
    fx_status: str | None = None
    fx_base_currency: str | None = None
    fx_quote_currency: str | None = None
    fx_is_stale: bool | None = None
    supplier_shipping_cost: Decimal | None = None
    shipping_cost_available: bool = False
    handling_cost: Decimal
    fee_estimate: Decimal | None = None
    sell_price: Decimal | None = None
    compare_at_price: Decimal | None = None
    proposed_sell_price: Decimal | None = None
    profit: Decimal | None = None
    margin_percent: Decimal | None = None
    break_even_price: Decimal | None = None
    pricing_rule_source: str | None = None
    manual_override: bool = False
    row_blocked: bool = False
    row_block_message: str | None = None


class DraftPricingWorkspaceRead(CamelCaseModel):
    product_id: uuid.UUID
    #: Destination selling currency (verified Shopify shop.currencyCode when linked).
    currency: str | None = None
    selling_currency: str | None = None
    selling_currency_source: str | None = None
    destination_store_id: uuid.UUID | None = None
    product_sell_price: Decimal | None = None
    cost_price_min: Decimal | None = None
    cost_price_max: Decimal | None = None
    shipping_cost: Decimal | None = None
    shipping_cost_available: bool = False
    shipping_warning: str | None = None
    fx_note: str
    pricing_blocked: bool = False
    pricing_block_code: str | None = None
    pricing_block_message: str | None = None
    fx_provider: str | None = None
    fx_status: str | None = None
    fx_rate: Decimal | None = None
    fx_base_currency: str | None = None
    fx_quote_currency: str | None = None
    fx_provider_timestamp: datetime | None = None
    fx_fetched_at: datetime | None = None
    fx_is_stale: bool | None = None
    variants: list[DraftVariantPricingRow] = Field(default_factory=list)


class DraftPricingApplyRequest(CamelCaseModel):
    mode: DraftPricingApplyMode
    markup_percent: Decimal | None = None
    markup_fixed: Decimal | None = None
    target_margin_percent: Decimal | None = None
    min_profit: Decimal | None = None
    min_sell_price: Decimal | None = None
    max_sell_price: Decimal | None = None
    sell_price: Decimal | None = None
    compare_at_price: Decimal | None = None
    variant_ids: list[uuid.UUID] | None = None
    round_to_cents: bool = True
    psychological_rounding: bool = False
    handling_cost: Decimal = Field(default=Decimal("0"))
    fee_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    include_shipping_in_cost: bool = True
    #: Optional override; otherwise resolved from store / tenant / product.
    destination_store_id: uuid.UUID | None = None
