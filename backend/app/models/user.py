"""User model.

Phase 1 changed this model in three ways, each required by the Phase 1
specification and each removing a column rather than adding beside it:

* ``full_name`` → ``first_name`` + ``last_name``. Separate fields are needed to
  address a person correctly ("Hi Adnan") without string-splitting a single
  field, which breaks on compound surnames and on names that do not follow a
  given-then-family order.
* ``email_verified_at`` → ``is_verified``. The timestamp was strictly more
  informative, but keeping both would be two representations of one fact that
  can disagree.
* The ``role`` enum column is gone, replaced by the ``user_roles`` association
  table. Keeping both would give a user's role two sources of truth — and when
  an authorization check reads the stale one, the failure is a privilege
  escalation.

None of these had deployed data behind them: migration ``0001`` had never been
applied to a live database when Phase 1 began.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase, SoftDeleteMixin


class User(IdentifiedBase, SoftDeleteMixin):
    """A person who can sign in to a tenant.

    Declares ``tenant_id`` explicitly rather than inheriting ``TenantMixin`` so
    that the uniqueness constraint on ``(tenant_id, email)`` can live here: the
    same human may hold accounts in two different tenants using one email
    address, which a global unique index on email would wrongly prevent.
    """

    __tablename__ = "users"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)  # RFC 5321 max

    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Nullable: a user provisioned by invitation or by an SSO provider never
    # sets a local password. Never exposed through any schema.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Whether the account may authenticate at all. Distinct from tenant status:
    # a suspended tenant blocks every user, this blocks one.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Whether the email address has been confirmed. Phase 1 sets this to true on
    # registration because no mail delivery exists yet; the verification flow
    # that makes it meaningful is a later phase.
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Scoped to the tenant, for the reason in the class docstring.
        # Soft-deleted users still occupy their email within the tenant; freeing
        # it would let a re-invited address collide with audit history.
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
        # Supports the login lookup, which filters on email within a tenant and
        # excludes deleted rows.
        Index("ix_users_tenant_active", "tenant_id", "deleted_at"),
    )

    @property
    def full_name(self) -> str | None:
        """Display name assembled from the parts.

        A read-only property, not a column. It replaces the Phase 0 field for
        presentation purposes while leaving the stored data normalised, so
        nothing can write a ``full_name`` that disagrees with its components.
        """
        parts = [part for part in (self.first_name, self.last_name) if part]
        return " ".join(parts) if parts else None

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


__all__ = ["User"]
