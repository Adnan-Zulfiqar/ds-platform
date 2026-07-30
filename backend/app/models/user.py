"""User model.

Phase 0 defines the persistence shape only. No authentication logic exists yet —
no password hashing, no token issuing, no login endpoint. Those arrive in the
auth phase. The model is defined now because ``tenant_id`` relationships and
audit columns across the platform need a user table to reference, and retrofitting
it later would touch every table added in between.

``password_hash`` is nullable on purpose: users created through an invite or an
SSO provider never set a local password.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase, SoftDeleteMixin


class UserRole(StrEnum):
    """Role within a tenant.

    A flat role enum is sufficient for Phase 0. When granular permissions are
    needed, this becomes the default role assigned to a permission set rather
    than being replaced — existing rows keep meaning what they meant.
    """

    OWNER = "owner"  # billing control, cannot be removed
    ADMIN = "admin"  # full operational access
    MEMBER = "member"  # day-to-day operations
    VIEWER = "viewer"  # read-only


class User(IdentifiedBase, SoftDeleteMixin):
    """A person who can sign in to a tenant.

    Like ``Tenant``, this does not use ``TenantScopedBase``. It declares
    ``tenant_id`` explicitly so the uniqueness constraint on
    ``(tenant_id, email)`` can be expressed here: the same human may hold
    accounts in two different tenants using one email address, which a global
    unique index on email would wrongly prevent.
    """

    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)  # RFC 5321 max
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Populated by the auth phase. Never exposed through any schema.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True, validate_strings=True),
        nullable=False,
        default=UserRole.MEMBER,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Scoped to the tenant, for the reason described in the class docstring.
        # Soft-deleted users still occupy their email within the tenant; freeing
        # it would let a re-invited address collide with audit history.
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
        # Supports the login lookup, which filters on email within a tenant and
        # excludes deleted rows.
        Index("ix_users_tenant_active", "tenant_id", "deleted_at"),
    )

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
