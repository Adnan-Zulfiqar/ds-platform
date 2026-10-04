"""Subscription state per workspace (Track E6).

**Stripe is the source of truth.** This row mirrors what Stripe says. Webhooks
and an explicit sync after checkout write it, and nothing in DropPilot
decides on its own that a customer has paid.

One row per workspace, created on first look with the trial window: the
workspace's creation time plus ``STRIPE_TRIAL_DAYS``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase, TenantScopedBase


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


class TrialFingerprint(IdentifiedBase):
    """A store that has already had a free trial (Track E6b, D-016).

    Not tenant data: a one-way hash of the store's public identity (Shopify
    shop domain, eBay seller id, WooCommerce site), and the workspace that
    first used it. It is kept when that workspace is closed, because the
    point is that a new account cannot repeat the trial with the same store.
    """

    __tablename__ = "trial_fingerprints"

    #: sha256 of ``"<platform>:<identity>"``, lower-cased.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    first_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["TenantSubscription", "TrialFingerprint"]
