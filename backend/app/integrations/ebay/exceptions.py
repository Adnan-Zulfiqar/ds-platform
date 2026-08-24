"""eBay integration errors.

Typed and mapped to the status codes eBay's contract expects, because the
status *is* the protocol here: 412 tells eBay the notification was rejected,
and a 5xx tells it to resend. Getting either wrong is a compliance failure, not
a cosmetic one.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AppError, ExternalServiceError, ValidationError

SERVICE_NAME = "ebay"


class EbayError(ExternalServiceError):
    code = "ebay_error"
    message = "The eBay integration encountered an error."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, service=SERVICE_NAME, details=details)


class EbayNotConfiguredError(ValidationError):
    """The compliance endpoint cannot answer because configuration is missing.

    Fails closed rather than hashing an empty verification token: that would
    produce a well-formed digest that eBay rejects, and the operator would see
    only "endpoint validation failed" with nothing pointing at the cause.
    """

    code = "ebay_not_configured"
    message = "eBay marketplace account deletion is not configured on this server."


class EbayChallengeError(ValidationError):
    """The challenge request itself was malformed."""

    code = "ebay_challenge_invalid"
    message = "The eBay challenge request was not valid."


class EbaySignatureError(AppError):
    """The notification's signature did not verify.

    **412 by eBay's contract**, not 401. eBay's own SDKs return
    *"HTTP status 412 - Precondition Failed"* when signature verification
    fails, and matching that is what makes this endpoint behave the way eBay's
    tooling expects.
    """

    code = "ebay_signature_invalid"
    status_code = 412
    message = "The eBay notification signature could not be verified."


class EbayNotificationRejectedError(AppError):
    """The payload was not a notification this endpoint accepts.

    Also 412: eBay's contract has exactly one rejection status for a delivery
    that will never be acceptable, and inventing a second would leave eBay
    retrying something that can never succeed.
    """

    code = "ebay_notification_rejected"
    status_code = 412
    message = "The eBay notification payload was not accepted."


class EbayNotificationConflictError(AppError):
    """The same ``notificationId`` arrived carrying a different payload.

    eBay's notification id is the idempotency key, so two deliveries that share
    one must be the same notification. When the payload digest differs they are
    not, and there is no safe reading of that: acknowledging would silently
    discard a real deletion instruction, because eBay never resends an
    acknowledged notification; re-running the erasure would repeat destructive
    work under an identity that has already been settled.

    **409**, which eBay treats as a failed delivery and retries. That is the
    honest outcome — the endpoint genuinely cannot act on it — and the retry
    costs nothing if the conflict was transient. If it is not, an operator has a
    ledger row and a stable code to investigate with, rather than silence.

    The differing payloads are never included. Both are unauthenticated input
    on a public route and one of them carries personal data.
    """

    code = "ebay_notification_conflict"
    status_code = 409
    message = "That eBay notification identifier was already received with different content."


class EbayPayloadTooLargeError(AppError):
    """The body exceeded the documented ceiling.

    413 rather than 412: this one *is* worth a retry from a correctly behaving
    sender, and it distinguishes an abusive body from a genuine notification
    that failed verification.
    """

    code = "ebay_payload_too_large"
    status_code = 413
    message = "The eBay notification body exceeded the permitted size."


class EbayKeyUnavailableError(EbayError):
    """eBay's key service or OAuth could not be reached.

    Deliberately a 502-class failure so eBay **resends**. Acknowledging a
    notification whose signature could not be checked would mean accepting
    unverified deletion instructions, and losing one is worse than delaying it:
    eBay retries for 24 hours before marking the endpoint down.
    """

    code = "ebay_key_unavailable"
    message = "The eBay notification key service is temporarily unavailable."


__all__ = [
    "SERVICE_NAME",
    "EbayChallengeError",
    "EbayError",
    "EbayKeyUnavailableError",
    "EbayNotConfiguredError",
    "EbayNotificationRejectedError",
    "EbayPayloadTooLargeError",
    "EbaySignatureError",
]
