"""eBay compliance ledger.

One row per eBay notification, and **no personal data in any of them**.

The ledger exists for exactly two reasons: duplicate delivery must not repeat
destructive processing, and a compliance auditor has to be able to see that a
deletion request was received and acted on. Neither needs the merchant's
username, their eBay user id, their EIAS token or the payload — so none of
those columns exist. A table that cannot hold personal data cannot leak it, and
cannot itself become something that has to be erased when the next deletion
request arrives.

What replaces them is ``payload_digest``: a SHA-256 of the exact bytes eBay
sent. It proves the same notification was seen without retaining what it said.

Deliberately **not** ``TenantScopedBase``. An eBay account deletion is an
instruction from eBay about a person, not about one workspace, and the same
person may have data under several tenants — or, as of EBAY-C0, none at all.
Scoping this to a tenant would mean either inventing one or writing several
rows for one notification, and both make the idempotency key wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase


class NotificationVerification(StrEnum):
    """Whether the signature was proven before anything else happened."""

    VERIFIED = "verified"
    #: Recorded, never acknowledged. A rejected notification is not written to
    #: the ledger at all today; the member exists so a future phase that wants
    #: to count rejections has somewhere honest to put them.
    REJECTED = "rejected"


class NotificationProcessing(StrEnum):
    """How far the deletion work got.

    ``COMPLETED`` is the only state that may be acknowledged to eBay. The
    others exist so a retry can tell "already done" from "started and died".
    """

    RECEIVED = "received"
    COMPLETED = "completed"
    FAILED = "failed"


class EbayComplianceNotification(IdentifiedBase):
    """A received eBay compliance notification, recorded without its contents."""

    __tablename__ = "ebay_compliance_notifications"

    __table_args__ = (
        Index(
            "ix_ebay_compliance_notifications_status",
            "processing_status",
            "last_received_at",
        ),
    )

    #: eBay's own identifier. **Unique** — this is the idempotency key, and the
    #: constraint is what makes concurrent duplicate deliveries safe: the
    #: database refuses the second insert rather than two workers both deciding
    #: they are first.
    notification_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    topic: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    #: eBay's timestamps, kept because they are about the *event*, not the
    #: person: when the deletion was requested and when eBay published it.
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: SHA-256 of the exact received bytes. Proof of identity without content:
    #: a digest cannot be reversed into the username it covered.
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    verification_status: Mapped[NotificationVerification] = mapped_column(
        Enum(
            NotificationVerification,
            name="ebay_notification_verification",
            native_enum=True,
            validate_strings=True,
            # Persist the member *value*, not its name — SQLAlchemy's default
            # is the name, which does not match the lowercase labels the
            # migration creates and fails every insert at runtime.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    processing_status: Mapped[NotificationProcessing] = mapped_column(
        Enum(
            NotificationProcessing,
            name="ebay_notification_processing",
            native_enum=True,
            validate_strings=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )

    first_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: How many times eBay has delivered this notification to us. Distinct from
    #: eBay's own ``publishAttemptCount``, which counts its sends rather than
    #: our receipts.
    receipt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    #: How many owning records were erased. Zero is the expected value until
    #: EBAY-C1 stores eBay data, and recording it is what turns "we have
    #: nothing to delete" from an assumption into evidence.
    erased_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: A stable machine code — never a message containing user data.
    outcome_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Reserved for an operator-facing note. Written only with fixed strings;
    #: nothing derived from the payload ever reaches it.
    outcome_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class EbayConnectionStatus(StrEnum):
    """Lifecycle of a seller's eBay authorization.

    ``RECONNECT_REQUIRED`` is separate from ``ERROR`` on purpose. eBay revokes
    refresh tokens for entirely ordinary reasons — the seller changed their
    password, or their eBay login name — and presenting that as a fault leads
    merchants to open support tickets for something one button fixes. The two
    states also differ in what the platform should do: one is retryable, the
    other never is.
    """

    PENDING = "pending"
    CONNECTED = "connected"
    RECONNECT_REQUIRED = "reconnect_required"
    ERROR = "error"


class EbayConnection(IdentifiedBase):
    """A tenant's authorized eBay seller account.

    **The identity column is ``ebay_user_id``, and it is eBay's immutable id.**
    eBay's specification says of it: "The eBay immutable user ID of the user's
    account and can always be used to identify the user" — and of ``username``:
    "This value can be changed by the user." Keying on the name would mean that
    one rename makes the same seller look like a new account: reconnecting would
    create a second row, and the deletion contract would no longer be able to
    find what it must erase.

    **Uniqueness is global, not per tenant.** ``ebay_user_id`` is unique across
    the whole table, so one eBay seller cannot be attached to two workspaces.
    That is enforced by the database rather than by a lookup, which matters for
    two reasons: a check-then-insert races, and a cross-tenant *lookup* would be
    an existence oracle — a caller could learn whether a given seller uses the
    platform by watching which error came back. Here the conflict surfaces only
    as an integrity violation on insert, and the message says nothing about who
    holds it.

    **No soft delete**, matching ``AliExpressConnection``. Disconnecting removes
    the row and its ciphertext outright. Keeping an encrypted copy of
    credentials a seller asked us to forget is retention nobody requested, and
    it widens the blast radius of a future key compromise for no benefit.
    """

    __tablename__ = "ebay_connections"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: Who authorised it. SET NULL rather than CASCADE: removing a user must not
    #: destroy a working workspace-wide integration that happens to bear their
    #: name.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: Which eBay estate the tokens belong to. A sandbox token is useless
    #: against production and vice versa, so the environment is stored with the
    #: credentials rather than assumed from current configuration — which can
    #: change under a connection that already exists.
    environment: Mapped[str] = mapped_column(String(16), nullable=False)

    #: eBay's immutable account identifier. **This is eBay personal data**, and
    #: it is why EBAY-C1 must register a deletion owner — see
    #: ``app.integrations.ebay.deletion``.
    ebay_user_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Display only, and refreshed on every verify. Never used to match rows.
    ebay_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: ``registrationMarketplaceId`` from ``getUser`` — eBay's own answer, not a
    #: guess from locale or currency.
    marketplace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: ``BUSINESS`` or ``INDIVIDUAL``. Recorded because listing eligibility and
    #: tax treatment differ, and the later milestones will need it.
    account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- Ciphertext columns ------------------------------------------------
    #
    # There is no plaintext token column on this model, and no property that
    # returns one. Sized for Fernet output (base64, ~1.4x plaintext plus
    # overhead) over eBay's tokens, which are long.
    encrypted_access_token: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: eBay supplies ``refresh_token_expires_in`` on the authorization-code
    #: grant (around 18 months) but not on refresh, so this is set once at
    #: consent and left alone.
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: The space-separated scope string actually granted, as eBay reported it —
    #: not the set that was requested. They can differ, and acting on the
    #: request rather than the grant is how an integration ends up calling an
    #: endpoint it was never authorised for.
    granted_scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[EbayConnectionStatus] = mapped_column(
        Enum(
            EbayConnectionStatus,
            name="ebay_connection_status",
            native_enum=True,
            validate_strings=True,
            # Persist the value, not the member name — see docs/Database.md.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=EbayConnectionStatus.PENDING,
        index=True,
    )

    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: A stable machine code explaining why reconnection is needed — never a
    #: raw upstream message, which could echo a credential into the database.
    reconnect_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Operator-facing failure text, generated by this application only.
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        # One workspace, one eBay seller account. Multiple accounts per tenant
        # is a plausible future need, but supporting it now would mean guessing
        # how listings route between them. Relaxing this later drops a
        # constraint; tightening it later would mean reconciling duplicates.
        UniqueConstraint("tenant_id", name="uq_ebay_connections_tenant_id"),
        # The cross-tenant guarantee. Global, so the database refuses a second
        # tenant claiming the same seller without anyone having to look first.
        UniqueConstraint("ebay_user_id", name="uq_ebay_connections_ebay_user_id"),
        # Supports the deletion path, which searches by identifier across all
        # tenants, and any future token-expiry sweep.
        Index("ix_ebay_connections_status_expiry", "status", "access_token_expires_at"),
    )

    @property
    def is_access_token_expired(self) -> bool:
        """Whether the access token is past its expiry.

        No recorded expiry counts as expired: acting on a token of unknown
        validity risks a failure that looks like an outage, and refreshing
        unnecessarily is cheap.
        """
        if self.access_token_expires_at is None:
            return True
        return datetime.now(UTC) >= self.access_token_expires_at

    def access_token_expires_within(self, seconds: int) -> bool:
        """Whether the access token expires inside a window."""
        if self.access_token_expires_at is None:
            return True
        return (self.access_token_expires_at - datetime.now(UTC)).total_seconds() <= seconds

    @property
    def needs_reconnect(self) -> bool:
        return self.status is EbayConnectionStatus.RECONNECT_REQUIRED

    @property
    def is_usable(self) -> bool:
        """Whether an authenticated eBay call can be made right now.

        A connection whose access token has expired is still *usable* when a
        refresh token remains: the token authority will renew it. What makes a
        connection unusable is having no refresh path left.
        """
        return (
            self.status is EbayConnectionStatus.CONNECTED
            and self.encrypted_refresh_token is not None
        )

    def __repr__(self) -> str:
        # No ciphertext and no eBay identifier: this reaches logs.
        return f"<EbayConnection id={self.id} tenant_id={self.tenant_id} status={self.status}>"


__all__ = [
    "EbayComplianceNotification",
    "EbayConnection",
    "EbayConnectionStatus",
    "NotificationProcessing",
    "NotificationVerification",
]
