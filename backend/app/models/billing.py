"""Subscription state per workspace (Track E6).

**Stripe is the source of truth.** This row mirrors what Stripe says. Webhooks
and an explicit sync after checkout write it, and nothing in DropPilot
decides on its own that a customer has paid.

One row per workspace, created on first look with the trial window: the
workspace's creation time plus ``STRIPE_TRIAL_DAYS``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase


class TenantSubscription(TenantScopedBase):
    __tablename__ = "tenant_subscriptions"

    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_subscriptions_tenant"),
        UniqueConstraint("stripe_customer_id", name="uq_tenant_subscriptions_customer"),
        UniqueConstraint("stripe_subscription_id", name="uq_tenant_subscriptions_subscription"),
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: ``starter`` / ``growth`` / ``pro``, or null before any subscription.
    plan: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ai_addon: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Stripe's subscription status, or ``none`` before any subscription.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none", server_default="none"
    )
    #: End of DropPilot's own free trial (no card).
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    stripe_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["TenantSubscription"]
