"""eBay compliance API schemas — no credential fields, no PII fields.

``ChallengeResponse`` is the only thing this endpoint ever returns to eBay. It
has exactly one field and no way to hold the verification token, so the token
cannot reach a response by accident.

The notification payload is parsed into a dataclass rather than a response
model: it is never serialised back out, and the identifiers inside it must not
end up in an OpenAPI schema where they would look like something this
application stores.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, Field

from app.integrations.ebay.deletion import DeletionSubject
from app.integrations.ebay.exceptions import EbayNotificationRejectedError
from app.models.ebay import EbayConnectionStatus
from app.schemas.base import CamelCaseModel

#: The only topic this endpoint accepts. Anything else fails closed — an
#: unknown topic means eBay is sending something this code has never been
#: designed to act on, and acknowledging it would claim otherwise.
MARKETPLACE_ACCOUNT_DELETION: Final = "MARKETPLACE_ACCOUNT_DELETION"

#: Schema versions this parser understands. eBay's contract is 1.0 today; a new
#: major version is a change to act on, not to absorb silently.
_SUPPORTED_SCHEMA_VERSIONS: Final = frozenset({"1.0"})

#: Domain separator, mixed into every identity digest.
#:
#: Without it, a digest computed here could in principle equal one computed for
#: some other purpose over the same values. It costs nothing and it means these
#: digests are only ever comparable with each other. The trailing ``/v2`` is the
#: algorithm version: change any part of what is covered, or how it is encoded,
#: and this string must change with it — a digest that silently means something
#: new is exactly how EBAY-C0.1 happened.
_IDENTITY_DIGEST_DOMAIN: Final = "droppilot/ebay/marketplace-account-deletion/identity/v2"

#: Marks a stored digest as an identity digest rather than a pre-EBAY-C0.1
#: raw-body digest.
#:
#: The ledger column is ``VARCHAR(64)``, exactly the width of a SHA-256 hex
#: string, so there is no room for a tag beside a full digest. The truncation is
#: worth it: a stored value that does not say which algorithm produced it is
#: precisely what made the C0.1 defect ambiguous to recover from. ``:`` is not a
#: hex character, so a legacy digest can never be mistaken for one of these.
#:
#: 61 hex characters is 244 bits. This digest detects an eBay notification id
#: being reused for different event content; it is not a security boundary, and
#: the payload it summarises has already been signature-verified.
IDENTITY_DIGEST_PREFIX: Final = "v2:"
_IDENTITY_DIGEST_HEX_LENGTH: Final = 61


def _digest_field(value: str | None) -> bytes:
    """Encode one field so no two different field lists can ever collide.

    Concatenating values with a separator is the obvious approach and is wrong:
    ``["ab", "c"]`` and ``["a", "bc"]`` collide, and any separator character can
    appear inside a value that an upstream system controls. Each field is
    therefore tagged present/absent and length-prefixed, which makes the
    encoding injective — the original list can be read back out of the bytes, so
    two different lists cannot produce the same bytes.

    ``None`` is distinct from ``""``: an absent ``eventDate`` and an empty one
    are different facts, and collapsing them would let one impersonate the
    other.
    """
    if value is None:
        return b"\x00"
    encoded = value.encode("utf-8")
    return b"\x01" + len(encoded).to_bytes(8, "big") + encoded


class ChallengeResponse(BaseModel):
    """eBay's endpoint-validation reply.

    Serialised by the real JSON encoder rather than assembled as a string —
    eBay's guide is explicit that hand-built bodies often acquire a byte order
    mark, *"a BOM is considered invalid JSON"*, and the subscription then fails
    with no useful diagnostic.
    """

    challenge_response: str = Field(serialization_alias="challengeResponse")

    model_config = {"populate_by_name": True}


@dataclass(frozen=True, slots=True)
class MarketplaceAccountDeletion:
    """One parsed notification.

    ``subject`` carries the identifiers for the duration of processing only.
    Nothing on this object is persisted except ``notification_id``, ``topic``,
    ``schema_version``, the two timestamps and ``identity_digest``.
    """

    notification_id: str
    topic: str
    schema_version: str
    event_date: datetime | None
    publish_date: datetime | None
    publish_attempt_count: int | None
    subject: DeletionSubject

    @property
    def identity_digest(self) -> str:
        """What makes two deliveries the *same* notification.

        **Covers the immutable event, not the delivery.** eBay documents
        ``publishDate`` as *"A timestamp indicating when the current
        notification was sent"* and ``publishAttemptCount`` as *"An integer
        indicating how many times the notification has been sent to this
        specific callback URL"* — both describe an attempt, and both change when
        eBay resends. Including them, which a digest of the raw body does, makes
        every retry look like a different notification. That is the EBAY-C0.1
        defect: eBay retried, the endpoint answered 409, and eBay retried again.

        **Covers no personal data, deliberately.** The subject identifiers —
        ``userId``, ``username``, ``eiasToken`` — are *not* in here. An earlier
        draft of this fix included them, and that was wrong: the ledger row is a
        permanent compliance receipt that is never erased, so a digest over
        those values would leave behind a way to confirm, forever, that a named
        person's account was deleted. There is no keyed hashing authority in
        this codebase to blunt that, and a low-entropy username under an unkeyed
        hash is a confirmation oracle. The ledger's whole design is that it
        cannot hold personal data; this keeps that true.

        What is covered — topic, schema version, ``notificationId`` and
        ``eventDate`` (*"when the eBay user made the data deletion request"*) —
        is **already stored in plaintext in the same row**, as
        ``topic``, ``schema_version``, ``notification_id`` and ``event_date``.
        So the digest introduces no retention that the row did not already have.

        **What this cannot detect**, stated plainly: a notification id reused for
        a *different person* with a byte-identical ``eventDate``. Detecting that
        would require the subject in the digest, at the privacy cost above. The
        residual risk is small — ``eventDate`` is millisecond-precision, eBay
        documents ``notificationId`` as unique, and the payload is
        signature-verified before it reaches here, so a collision would be a
        provider bug rather than an attack — and it is the right side of that
        trade.

        Built from **parsed** values, so key ordering, whitespace and equivalent
        timestamp spellings cannot make one delivery look unlike another, and
        encoded with :func:`_digest_field` so no two different field lists can
        collide.
        """
        canonical = b"".join(
            _digest_field(value)
            for value in (
                _IDENTITY_DIGEST_DOMAIN,
                self.topic,
                self.schema_version,
                self.notification_id,
                _utc_isoformat(self.event_date),
            )
        )
        digest = hashlib.sha256(canonical).hexdigest()
        return f"{IDENTITY_DIGEST_PREFIX}{digest[:_IDENTITY_DIGEST_HEX_LENGTH]}"


def is_identity_digest(stored: str) -> bool:
    """Whether a stored digest came from :attr:`identity_digest`.

    Rows written before EBAY-C0.1 hold a SHA-256 of one delivery's raw bytes
    and are pure hex, so the tag is unambiguous. Used to tell "this row can be
    compared" from "this row predates the comparison being meaningful".
    """
    return stored.startswith(IDENTITY_DIGEST_PREFIX)


def _utc_isoformat(value: datetime | None) -> str | None:
    """Normalise to UTC before it reaches a digest.

    A naive datetime is read as UTC rather than as local time: eBay sends
    ``...Z``, and treating a parse that lost the marker as machine-local would
    make the same instant digest differently on two servers.
    """
    if value is None:
        return None
    normalised = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalised.isoformat()


def parse_notification(payload: object) -> MarketplaceAccountDeletion:
    """Validate eBay's payload, refusing anything unexpected.

    Field names and nesting are taken from the AsyncAPI contract published in
    the Marketplace Account Deletion guide: ``metadata.{topic, schemaVersion,
    deprecated}`` and ``notification.{notificationId, eventDate, publishDate,
    publishAttemptCount, data.{username, userId, eiasToken}}``.

    Every rejection raises the same typed error. The offending value is never
    included — it is attacker-controlled on an unauthenticated endpoint, and a
    reason code is enough to debug with.
    """
    if not isinstance(payload, dict):
        raise _reject("not_an_object")

    metadata = payload.get("metadata")
    notification = payload.get("notification")
    if not isinstance(metadata, dict):
        raise _reject("missing_metadata")
    if not isinstance(notification, dict):
        raise _reject("missing_notification")

    topic = metadata.get("topic")
    if not isinstance(topic, str) or not topic:
        raise _reject("missing_topic")
    if topic != MARKETPLACE_ACCOUNT_DELETION:
        # Fails closed. This endpoint is registered for one topic; anything
        # else arriving means a subscription changed without the code changing.
        raise _reject("unsupported_topic")

    schema_version = metadata.get("schemaVersion")
    if not isinstance(schema_version, str) or not schema_version:
        raise _reject("missing_schema_version")
    if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise _reject("unsupported_schema_version")

    notification_id = notification.get("notificationId")
    if not isinstance(notification_id, str) or not notification_id.strip():
        raise _reject("missing_notification_id")
    if len(notification_id) > 255:
        raise _reject("notification_id_too_long")

    data = notification.get("data")
    if not isinstance(data, dict):
        raise _reject("missing_data")
    subject = DeletionSubject(
        user_id=_optional_string(data.get("userId")),
        username=_optional_string(data.get("username")),
        eias_token=_optional_string(data.get("eiasToken")),
    )
    if not subject.has_any_identifier:
        # Without at least one identifier there is nothing a deletion could
        # match, so acknowledging would be claiming work that cannot happen.
        raise _reject("no_subject_identifier")

    return MarketplaceAccountDeletion(
        notification_id=notification_id.strip(),
        topic=topic,
        schema_version=schema_version,
        event_date=_optional_timestamp(notification.get("eventDate"), "event_date"),
        publish_date=_optional_timestamp(notification.get("publishDate"), "publish_date"),
        publish_attempt_count=_optional_int(notification.get("publishAttemptCount")),
        subject=subject,
    )


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_timestamp(value: Any, field: str) -> datetime | None:
    """Parse eBay's ISO-8601 ``...Z`` timestamps.

    Optional rather than required: the timestamps are useful context, and
    refusing a real deletion instruction because eBay changed a date format
    would trade a compliance obligation for a parsing preference. A malformed
    one is dropped and logged by shape, not by value.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _reject(reason: str) -> EbayNotificationRejectedError:
    return EbayNotificationRejectedError(details={"reason": reason})


# ---------------------------------------------------------------------------
# EBAY-C1: what the seller-connection endpoints return to this platform's own
# clients.
# ---------------------------------------------------------------------------


class EbayConnectionRead(CamelCaseModel):
    """Connection state as the integrations card shows it.

    **There is nowhere in this model to put a credential.** No access token, no
    refresh token, no ciphertext, no client secret, no verification token, and
    no raw provider payload. That is the structural guarantee: a token cannot
    leak through this endpoint by accident, because the response has no field
    capable of carrying one.

    ``ebayUsername`` is display-only and may be stale between verifies — eBay
    lets sellers change it. The immutable id is what the platform matches on,
    and it is *not* exposed: it is eBay personal data, the card has no use for
    it, and putting it in an API response would create a second place it has to
    be erased from.
    """

    id: uuid.UUID
    status: EbayConnectionStatus
    environment: str
    ebay_username: str | None = Field(
        default=None, description="Display name; the seller can change it on eBay."
    )
    marketplace_id: str | None = None
    account_type: str | None = None
    scopes: list[str] = Field(default_factory=list, description="Scopes eBay granted.")
    connected_at: datetime | None = None
    last_verified_at: datetime | None = None
    access_token_expires_at: datetime | None = None
    needs_reconnect: bool
    reconnect_reason: str | None = Field(
        default=None, description="Stable machine code; never upstream text."
    )
    last_error: str | None = None


class EbayStatusResponse(CamelCaseModel):
    """Response for the eBay status endpoint.

    ``configured`` and ``connected`` are separate, and the distinction matters
    to the card: a server with no eBay credentials cannot offer a Connect button
    at all, which is a different message from "nobody has connected yet".

    ``connected`` is computed server-side so every consumer agrees on what it
    means — a connection awaiting reconnection is *not* connected, and a client
    inferring that from the enum would get it wrong.
    """

    configured: bool = Field(description="Whether this server has eBay OAuth credentials.")
    connected: bool
    connection: EbayConnectionRead | None = None


class EbayAuthorizationResponse(CamelCaseModel):
    """The consent URL the browser should be sent to."""

    authorization_url: str
    state: str = Field(description="Opaque CSRF token; echoed back on callback.")
    expires_in_seconds: int


__all__ = [
    "IDENTITY_DIGEST_PREFIX",
    "MARKETPLACE_ACCOUNT_DELETION",
    "ChallengeResponse",
    "EbayAuthorizationResponse",
    "EbayConnectionRead",
    "EbayStatusResponse",
    "MarketplaceAccountDeletion",
    "is_identity_digest",
    "parse_notification",
]
