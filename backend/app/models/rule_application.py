"""Durable record of one confirmed bulk rule application (M3A-3).

A bulk reprice touches many rows and can partially fail. Without a durable
record there is no answer to "what did that run actually do", and a retry
cannot tell a repeat from a fresh request. Both are recorded here: the run,
and one row per item it touched or refused to touch.

Kept in its own module rather than added to ``pricing.py`` because the
lifecycle differs -- rules are long-lived configuration, applications are
immutable events -- and because ``pricing.py`` is already the largest model
file in the project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantScopedBase

_MONEY = Numeric(16, 4)


class ApplicationStatus(StrEnum):
    """Where a run got to.

    ``PARTIAL`` is a first-class outcome, not an error. A bulk reprice over a
    real catalogue will routinely succeed for most products and refuse a few
    for missing supplier data; collapsing that into "failed" would tell the
    merchant to redo work that already landed.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApplicationItemOutcome(StrEnum):
    APPLIED = "applied"
    #: Deliberately not written -- already at the proposed price, or the
    #: merchant did not select it.
    SKIPPED = "skipped"
    #: Held back because the inputs were not trustworthy. Carries a reason.
    NEEDS_REVIEW = "needs_review"
    #: The product changed between preview and confirmation.
    STALE = "stale"
    #: Published products are never repriced by this workflow.
    PUBLISHED = "published"
    FAILED = "failed"


class RuleApplication(TenantScopedBase):
    """One confirmed bulk application of pricing rules to drafts."""

    __tablename__ = "rule_applications"

    __table_args__ = (
        Index("ix_rule_applications_tenant_created", "tenant_id", "created_at"),
        Index("ix_rule_applications_tenant_status", "tenant_id", "status"),
        # The idempotency guarantee. A retry after a timeout must return the
        # original run rather than start a second one, and only a unique
        # constraint can settle that between two concurrent requests.
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_rule_applications_tenant_idempotency"
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Hash of the confirmed request. A retry carrying the *same* key but a
    #: different payload is a client bug, not a retry, and is refused rather
    #: than silently answered with the first run's result.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(
            ApplicationStatus,
            name="rule_application_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ApplicationStatus.PENDING,
        server_default=ApplicationStatus.PENDING.value,
    )
    pricing_rule_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: Version the merchant confirmed against. Revalidated before any write:
    #: a rule edited between preview and confirmation invalidates the run.
    pricing_rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shipping_rule_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    shipping_rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Exactly what was asked for -- selected ids, or the filter that stood in
    #: for them. Stored rather than referenced so the record still explains
    #: itself after the drafts it names are gone.
    selection: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applied_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    items: Mapped[list[RuleApplicationItem]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RuleApplicationItem(TenantScopedBase):
    """What happened to one product or variant inside a run.

    Every item gets a row, including the ones nothing happened to. A run that
    only recorded its successes would leave a merchant unable to answer "why
    is this product still at the old price", which is the question they
    actually ask.
    """

    __tablename__ = "rule_application_items"

    __table_args__ = (
        Index("ix_rule_application_items_tenant_app", "tenant_id", "application_id"),
        Index("ix_rule_application_items_tenant_product", "tenant_id", "product_id"),
        # One row per (run, product, variant). Makes a retry that re-enters
        # the same run unable to double-write its own results.
        UniqueConstraint(
            "application_id",
            "product_id",
            "variant_id",
            name="uq_rule_application_items_target",
        ),
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rule_applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Nullable so a run can report an id that no longer resolves -- a draft
    #: deleted between preview and confirmation, or an id from another
    #: tenant. Those must appear in the results with a reason rather than
    #: being dropped, and a NOT NULL column with a foreign key cannot hold
    #: them. The id itself goes in `message`.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    outcome: Mapped[ApplicationItemOutcome] = mapped_column(
        Enum(
            ApplicationItemOutcome,
            name="rule_application_item_outcome",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    previous_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    new_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    landed_cost: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    applied_rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Machine-readable, from the pricing and shipping review vocabularies, so
    #: the UI can map each to a specific "here is what to do".
    review_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    application: Mapped[RuleApplication] = relationship(back_populates="items")


__all__ = [
    "ApplicationItemOutcome",
    "ApplicationStatus",
    "RuleApplication",
    "RuleApplicationItem",
]
