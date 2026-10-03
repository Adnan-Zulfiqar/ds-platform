"""In-app notifications.

Produced by services and Celery tasks when something an operator should see
happens. The frontend notification centre reads these through the API; the
Zustand store holds only UI state (open/closed), never the server list.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase


class NotificationKind(StrEnum):
    """Stable machine-readable kinds the UI branches on."""

    IMPORT_COMPLETED = "import_completed"
    SYNC_FAILED = "sync_failed"
    INVENTORY_CHANGED = "inventory_changed"
    PRICE_CHANGED = "price_changed"
    ORDER_IMPORTED = "order_imported"
    SHIPMENT_UPDATED = "shipment_updated"
    WEBHOOK_FAILURE = "webhook_failure"
    TASK_FAILURE = "task_failure"
    AUTOMATION_COMPLETED = "automation_completed"
    AUTOMATION_FAILED = "automation_failed"
    INFO = "info"


class Notification(TenantScopedBase):
    """One notification for a tenant (optionally targeted at a user)."""

    __tablename__ = "notifications"

    __table_args__ = (
        Index("ix_notifications_tenant_unread", "tenant_id", "is_read", "created_at"),
        Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
        # The E3 sweep reads only pending rows, per tenant.
        Index(
            "ix_notifications_tenant_email_pending",
            "tenant_id",
            "created_at",
            postgresql_where=text("email_status = 'pending'"),
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind: Mapped[NotificationKind] = mapped_column(
        Enum(
            NotificationKind,
            name="notification_kind",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=NotificationKind.INFO,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    href: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Track E3 outbox: ``pending`` when written, then ``sent``, ``skipped``
    #: (nobody opted in) or ``failed``. Null on rows from before E3, which
    #: are never emailed retroactively.
    email_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationEmailPreference(TenantScopedBase):
    """Which notification kinds a user wants by email (Track E3).

    No row means the defaults (failures only): a new user is not mailed about
    every import, and nobody is left unaware that a sync is broken.
    """

    __tablename__ = "notification_email_preferences"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", name="uq_notification_email_preferences_tenant_user"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: NotificationKind values, as strings.
    kinds: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


__all__ = ["Notification", "NotificationEmailPreference", "NotificationKind"]
