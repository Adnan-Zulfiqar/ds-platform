"""Platform operators and their audit trail (Track E5, decision D-015).

A platform admin is **not** a user of any tenant: no ``tenant_id``, no roles
in ``user_roles``, no path from a tenant login to this table. That separation
is the point of option A. A bug in tenant role checks cannot make anyone a
platform admin, because the two identities never share a table or a token.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase, SoftDeleteMixin


class PlatformAdmin(IdentifiedBase, SoftDeleteMixin):
    __tablename__ = "platform_admins"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The TOTP seed, encrypted with the platform key. Never returned.
    encrypted_totp_secret: Mapped[str] = mapped_column(Text, nullable=False)
    #: The last accepted TOTP time step: a code is good once (RFC 6238 §5.2).
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: A ``PlatformRole`` value; what it may do is the matrix in
    #: ``app.core.platform_permissions``. Accounts that existed before roles
    #: (D-018) became ``super_admin``, which is what they could do already.
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="super_admin", server_default="super_admin"
    )


class PlatformAdminSession(IdentifiedBase):
    """One sign-in. A platform token carries its id (``sid``), and every
    request checks the session is still open, so revoking it ends the token
    at once rather than at expiry."""

    __tablename__ = "platform_admin_sessions"

    admin_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_admins.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When the operator last re-entered password + code in this session;
    #: sensitive actions need it within ``REAUTH_WINDOW_MINUTES``.
    reauthenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PlatformAdminAudit(IdentifiedBase):
    """Append-only. The application has no update or delete path for it; the
    repository exposes ``append`` and reads only."""

    __tablename__ = "platform_admin_audit"

    admin_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_admins.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: e.g. ``login_succeeded``, ``login_failed``, ``tenant_suspended``.
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Added by D-018. Null on rows written before it.
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: ``success`` or ``failure`` (a refused sign-in, a denied permission).
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, default="success", server_default="success"
    )
    #: What the action was about, beyond a workspace: ``operator``,
    #: ``session`` ...; with the id as text.
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PlatformSupportSession(IdentifiedBase):
    """A time-limited window in which one operator may change one workspace
    (D-019). Opened with a reason and a re-authentication, visible to the
    workspace as a notification, and closed by expiry or by the operator.
    Every workspace change checks for an open one."""

    __tablename__ = "platform_support_sessions"

    admin_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("platform_admins.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Not a foreign key to a tenant-owned row: ``tenants`` sits above the
    #: boundary, and the session belongs to the operator, not the workspace.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = [
    "PlatformAdmin",
    "PlatformAdminAudit",
    "PlatformAdminSession",
    "PlatformSupportSession",
]
