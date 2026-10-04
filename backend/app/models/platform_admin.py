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


__all__ = ["PlatformAdmin", "PlatformAdminAudit"]
