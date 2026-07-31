"""Email verification tokens.

Stores only a hash of the raw token — same rule as refresh tokens. The raw
value is emailed (or logged by the development mailer) once and never persisted.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase


class EmailVerificationToken(TenantScopedBase):
    """One outstanding verification challenge for a user."""

    __tablename__ = "email_verification_tokens"

    __table_args__ = (
        Index("ix_email_verification_tokens_tenant_user", "tenant_id", "user_id"),
        Index("ix_email_verification_tokens_hash", "token_hash", unique=True),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
