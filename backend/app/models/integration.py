"""Marketplace integration connections.

Phase 3 covers AliExpress. The table is named for it rather than being a generic
``integrations`` table with a ``provider`` column, because the two are not the
same shape: each marketplace has different credential fields, different token
semantics, and different rate limits. A generic table would either become a bag
of nullable columns or a JSON blob that no constraint can protect.

When Shopify and eBay arrive they get their own tables. The parts genuinely
shared — status vocabulary, encryption, the repository base — are shared as code.

**Every credential column holds ciphertext.** The columns are named
``encrypted_*`` so a reader cannot mistake them for plaintext, and the model
exposes no property that returns a decrypted value: decryption is explicit, at
the point of use, through ``app.core.encryption``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase


class IntegrationStatus(StrEnum):
    """Lifecycle of a marketplace connection.

    ``PENDING`` covers the window between issuing an authorization URL and the
    callback returning. A connection stuck there means the user abandoned the
    consent screen, which is worth showing differently from an outright failure.
    """

    PENDING = "pending"
    CONNECTED = "connected"
    EXPIRED = "expired"
    ERROR = "error"


class AliExpressConnection(IdentifiedBase):
    """A tenant's connection to the AliExpress Open Platform.

    Declares ``tenant_id`` explicitly rather than inheriting ``TenantMixin`` so
    the uniqueness constraint below can live on the model.

    **No soft delete, deliberately.** Disconnecting removes the row outright.
    Retaining an encrypted copy of credentials a customer has asked us to forget
    is data retention they did not request, and it enlarges the blast radius of
    any future key compromise for no benefit. The *event* is recorded in the
    application log; the secret is not kept.
    """

    __tablename__ = "aliexpress_connections"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Who authorised the connection. Retained for audit — "who connected this
    # account" is the first question asked when a sync starts failing.
    #
    # SET NULL rather than CASCADE: deleting a user must not silently destroy a
    # working tenant-wide integration that happens to bear their name.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The app key is a public identifier, not a secret — it travels in every
    # request URL. Stored in the clear so it can be displayed and queried.
    app_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- Ciphertext columns ------------------------------------------------
    #
    # Sized generously: Fernet output is base64 and roughly 1.4x the plaintext
    # plus ~100 bytes of overhead, and token lengths are set by the provider.
    # Nullable: the AliExpress *application* secret is platform-owned (env),
    # not a per-tenant credential. Historical rows may still hold ciphertext
    # until migration ``0009`` clears them; new connections leave this NULL.
    encrypted_app_secret: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    encrypted_access_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(
            IntegrationStatus,
            name="integration_status",
            native_enum=True,
            validate_strings=True,
            # Persist the member value ("connected"), not the name
            # ("CONNECTED"). SQLAlchemy's default is the name, which does not
            # match the lowercase labels the migration creates and fails every
            # insert at runtime — see docs/Database.md.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=IntegrationStatus.PENDING,
        index=True,
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Last failure reason, for display on the integrations page. Holds a message
    # this application generated — never a raw upstream payload, which could
    # echo credentials back into the database.
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        # One AliExpress connection per tenant. Multiple supplier accounts are a
        # real future requirement, but supporting them now would mean guessing
        # how orders route between them. Relaxing this later is a migration that
        # drops a constraint; tightening it later would mean reconciling
        # duplicate rows.
        UniqueConstraint("tenant_id", name="uq_aliexpress_connections_tenant_id"),
        # Supports the health-check job, which scans for connections whose token
        # is close to expiry.
        Index(
            "ix_aliexpress_connections_status_expiry",
            "status",
            "token_expiry",
        ),
    )

    @property
    def is_token_expired(self) -> bool:
        """Whether the access token is past its expiry.

        A connection with no expiry recorded is treated as expired: acting on a
        token whose validity is unknown risks a failed sync that looks like an
        outage. Refreshing unnecessarily is cheap; a silent failure is not.
        """
        if self.token_expiry is None:
            return True
        return datetime.now(UTC) >= self.token_expiry

    def expires_within(self, seconds: int) -> bool:
        """Whether the token expires within a window.

        Used to refresh proactively rather than waiting for a request to fail.
        """
        if self.token_expiry is None:
            return True
        return (self.token_expiry - datetime.now(UTC)).total_seconds() <= seconds

    @property
    def is_usable(self) -> bool:
        """Whether the connection can currently make authenticated calls."""
        return (
            self.status is IntegrationStatus.CONNECTED
            and self.encrypted_access_token is not None
            and not self.is_token_expired
        )

    def __repr__(self) -> str:
        # No credential material, not even the ciphertext. This reaches logs.
        return (
            f"<AliExpressConnection id={self.id} tenant_id={self.tenant_id} status={self.status}>"
        )


__all__ = ["AliExpressConnection", "IntegrationStatus"]
