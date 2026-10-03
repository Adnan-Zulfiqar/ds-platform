"""Team invitations (Track E4).

An administrator invites an address into their workspace with a role; the
recipient follows a link, chooses a password and becomes a user of that
workspace. Only a hash of the link's secret is stored, exactly as for refresh
and verification tokens: a database read must not yield a usable link.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase


class UserInvitation(TenantScopedBase):
    __tablename__ = "user_invitations"

    __table_args__ = (
        # At most one open invitation per address per workspace. Re-inviting
        # rotates the open row's secret instead of piling up live links.
        Index(
            "uq_user_invitations_tenant_email_open",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text(
                "accepted_at IS NULL AND revoked_at IS NULL AND deleted_at IS NULL"
            ),
        ),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    #: A RoleName value. Never ``owner``: ownership is not handed out by link.
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    #: SHA-256 of the link secret. The secret is 256 random bits, so a plain
    #: digest is not brute-forceable and needs no key.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The user the invitation became, for the audit trail.
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


__all__ = ["UserInvitation"]
