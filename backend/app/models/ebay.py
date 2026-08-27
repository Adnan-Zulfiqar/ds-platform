"""eBay compliance ledger.

One row per eBay notification, and **no personal data in any of them**.

The ledger exists for exactly two reasons: duplicate delivery must not repeat
destructive processing, and a compliance auditor has to be able to see that a
deletion request was received and acted on. Neither needs the merchant's
username, their eBay user id, their EIAS token or the payload — so none of
those columns exist. A table that cannot hold personal data cannot leak it, and
cannot itself become something that has to be erased when the next deletion
request arrives.

What replaces them is ``payload_digest``: a SHA-256 over the notification's
**immutable identity**, tagged ``v2:``. It proves the same notification was seen
without retaining what it said.

EBAY-C0.1 changed it twice over, and both changes matter here.

It used to cover the exact bytes eBay sent. Those bytes carry ``publishDate``
and ``publishAttemptCount`` — fields eBay documents as changing on every
delivery attempt — so a raw-body digest identifies a *delivery*, and every
legitimate retry looked like a different notification and was refused with 409
while eBay kept resending.

And it deliberately covers **no personal data**. The subject identifiers are not
in it. This row is a permanent compliance receipt that is never erased, so a
digest over ``username``, ``userId`` or ``eiasToken`` would leave behind a way
to confirm, forever, that a named person's account was deleted — and there is no
keyed hashing authority in this codebase to blunt that. What is covered — topic,
schema version, ``notificationId`` and ``eventDate`` — is already stored in
plaintext in this same row, so the digest adds no retention the row did not
already have. See ``MarketplaceAccountDeletion.identity_digest`` for the
trade-off that follows from this, and
``EbayComplianceService._legacy_row_matches`` for how rows written under the old
algorithm are validated against their own columns.

Deliberately **not** ``TenantScopedBase``. An eBay account deletion is an
instruction from eBay about a person, not about one workspace, and the same
person may have data under several tenants — or, as of EBAY-C0, none at all.
Scoping this to a tenant would mean either inventing one or writing several
rows for one notification, and both make the idempotency key wrong.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Index, Integer, String, Text
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

    #: Proof of identity without content: a digest cannot be reversed into the
    #: username it covered.
    #:
    #: ``v2:`` + 61 hex characters is an identity digest (EBAY-C0.1 onwards); a
    #: bare 64-character hex string is a pre-C0.1 raw-body digest, which cannot
    #: be compared against a retry and is upgraded in place the first time one
    #: arrives. ``:`` is not a hex character, so the two can never be confused.
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


__all__ = [
    "EbayComplianceNotification",
    "NotificationProcessing",
    "NotificationVerification",
]
