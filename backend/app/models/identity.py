"""Federated sign-in identities: which external account maps to which user.

**Provider-neutral on purpose.** A `google_sub` column on `users` would work
today and would have to be migrated away the first time Apple or Microsoft
sign-in appears. A row per identity costs one table now and nothing later.

Deliberately **not** `TenantScopedBase`. An identity belongs to a *user*, and
the user carries the tenant; duplicating `tenant_id` here would create a second
copy of that fact which could drift from the first. Refresh tokens and role
grants are shaped the same way for the same reason.

Two unique constraints, answering different questions:

* `uq_user_identities_provider_subject` — **global**. One Google account cannot
  sign in as two different DropPilot users. Enforced by the database rather than
  by a lookup, because check-then-insert races, and a cross-tenant existence
  check would be an oracle telling an attacker whether an address is registered
  elsewhere.
* `uq_user_identities_user_provider` — one identity per provider per user.
  Linking a second Google account to the same user is a mistake, not a feature.

**The subject, never the email.** Google's `sub` is immutable; an email address
is not. Keying on email would mean a user who changes their Google address
becomes a stranger, and — worse — that whoever later acquires their old address
inherits the account.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class IdentityProvider(StrEnum):
    """Federated providers this platform accepts.

    A string column with a Python-side enum rather than a PostgreSQL enum type:
    adding Apple should be a code change and a value, not a migration that
    rewrites a type while the table is live.
    """

    GOOGLE = "google"


class UserIdentity(Base, UUIDPrimaryKeyMixin):
    """One external account linked to one DropPilot user."""

    __tablename__ = "user_identities"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    #: The provider's immutable subject identifier. Google documents `sub` as
    #: stable for the lifetime of the account; the email is not.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    #: The verified address the provider asserted when the identity was linked.
    #: Stored so the account page can say *which* Google account is attached
    #: when it differs from the DropPilot address, and so a later change is
    #: visible rather than silent. It is **not** an identifier and nothing
    #: matches on it.
    provider_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    last_authenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
        Index("ix_user_identities_provider_subject", "provider", "subject"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        # No subject and no address: this can reach a log line.
        return f"<UserIdentity id={self.id} user_id={self.user_id} provider={self.provider}>"
