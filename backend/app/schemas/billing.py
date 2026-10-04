"""Billing schemas (Track E6). No Stripe secret, card or customer detail is
ever part of a response; checkout and portal are Stripe-hosted pages."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas.base import CamelCaseModel


class PlanRead(CamelCaseModel):
    key: str
    name: str
    price_usd: int
    listing_limit: int
    ai_addon_usd: int


class BillingStatusRead(CamelCaseModel):
    configured: bool
    plan: str | None
    status: str
    ai_addon: bool
    trial_ends_at: datetime
    on_trial: bool
    paid: bool
    listing_limit: int
    listings_used: int
    can_write: bool
    can_use_ai: bool
    cancel_at_period_end: bool
    current_period_end: datetime | None
    has_customer: bool
    plans: list[PlanRead]


class PlanChoice(CamelCaseModel):
    plan: Literal["starter", "growth", "pro"]
    ai_addon: bool = False


class RedirectRead(CamelCaseModel):
    url: str


__all__ = ["BillingStatusRead", "PlanChoice", "PlanRead", "RedirectRead"]
