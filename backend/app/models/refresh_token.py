"""Refresh token records.

This table is what makes logout and session revocation real. Access tokens are
stateless and cannot be withdrawn before they expire; refresh tokens can,
because their fingerprint lives here.

**Only a hash is stored, never the token.** A stolen database dump must not
yield usable sessions. This mirrors password storage, with one deliberate
difference: the hash is SHA-256, not Argon2. A refresh token is 256 bits of
cryptographically random data, so it has no low-entropy structure for an
attacker to brute-force — the slow, memory-hard hashing that protects
human-chosen passwords buys nothing here and would add real latency to a call
made on every session renewal.

**No ``tenant_id`` column.** The lookup happens before identity is established:
a token is presented, and only after finding and validating this row does the
system know which tenant the caller belongs to. A tenant filter would have to be
satisfied before it could be derived. Tenant scoping is enforced instead by the
row's foreign key to a user, who belongs to exactly one tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class RefreshToken(Base, UUIDPrimaryKeyMixin):
    """A single issued refresh token.

    Inherits neither ``TimestampMixin`` nor ``SoftDeleteMixin``. It declares
    ``created_at`` directly because it needs no ``updated_at`` — a token is
    written once and only ever revoked — and ``revoked_at`` serves the purpose a
    soft delete would, with a name that says what it means.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 hex digest: always 64 characters. Unique because a collision would
    # mean two sessions share a credential.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # NULL means live. Set on logout, on rotation (the old token is revoked as
    # the new one is issued), and on reuse detection.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Supports "revoke every live token for this user", which runs on
        # password change and on reuse detection.
        Index("ix_refresh_tokens_user_active", "user_id", "revoked_at"),
        # Supports the expired-token cleanup job.
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    def is_usable(self, *, now: datetime | None = None) -> bool:
        """Whether this token may still be exchanged.

        Both conditions are checked here rather than at call sites so that no
        caller can validate one and forget the other.
        """
        return not self.is_revoked and not self.is_expired(now=now)

    def revoke(self, *, now: datetime | None = None) -> None:
        """Mark revoked. Idempotent — re-revoking preserves the original time.

        Preserving the first timestamp matters for incident analysis: it records
        when the session actually ended, not when someone last tried to use it.
        """
        if self.revoked_at is None:
            self.revoked_at = now or datetime.now(UTC)

    def __repr__(self) -> str:
        state = "revoked" if self.is_revoked else "active"
        return f"<RefreshToken id={self.id} user_id={self.user_id} {state}>"


__all__ = ["RefreshToken"]
