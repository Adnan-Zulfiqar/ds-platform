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

import uuid
from dataclasses import dataclass
from datetime import datetime
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
    ``schema_version`` and the two timestamps.
    """

    notification_id: str
    topic: str
    schema_version: str
    event_date: datetime | None
    publish_date: datetime | None
    publish_attempt_count: int | None
    subject: DeletionSubject


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
    "MARKETPLACE_ACCOUNT_DELETION",
    "ChallengeResponse",
    "EbayAuthorizationResponse",
    "EbayConnectionRead",
    "EbayStatusResponse",
    "MarketplaceAccountDeletion",
    "parse_notification",
]
