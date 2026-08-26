"""eBay integration errors.

Typed and mapped to the status codes eBay's contract expects, because the
status *is* the protocol here: 412 tells eBay the notification was rejected,
and a 5xx tells it to resend. Getting either wrong is a compliance failure, not
a cosmetic one.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AppError, ConflictError, ExternalServiceError, ValidationError

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


# --- EBAY-C1: seller OAuth connection ---------------------------------------


class EbayOAuthStateError(ValidationError):
    """The callback's ``state`` was missing, malformed, expired or replayed.

    One error for every one of those, deliberately. Distinguishing "expired"
    from "never existed" tells an attacker whether a guessed token was ever
    real, and there is nothing a legitimate seller does differently in response
    to the two — both mean "start again".
    """

    code = "ebay_oauth_state_invalid"
    message = "That eBay authorization request is no longer valid. Please start again."


class EbayConsentDeniedError(ValidationError):
    """The seller declined on eBay's consent page.

    Not a failure of this application, and reported as its own code so the card
    can say so plainly rather than showing a generic error the merchant will try
    to debug.
    """

    code = "ebay_consent_denied"
    message = "The eBay authorization request was declined, so nothing was connected."


class EbayTokenExchangeError(EbayError):
    """eBay refused a token request for a reason that might not recur."""

    code = "ebay_token_exchange_failed"
    message = "eBay could not complete the authorization. Please try connecting again."


class EbayTokenRevokedError(ValidationError):
    """The refresh token is no longer usable and consent must be granted again.

    eBay revokes refresh tokens when a seller changes their login name or
    password, revokes consent, or eBay itself revokes them. None of those is
    retryable, so this is a distinct type: the connection is marked
    reconnect-required rather than retried into a loop.
    """

    code = "ebay_reconnect_required"
    message = "The eBay authorization has expired or been revoked. Please reconnect."


class EbayIdentityUnavailableError(EbayError):
    """``getUser`` did not return a usable immutable identity."""

    code = "ebay_identity_unavailable"
    message = "eBay did not return the seller account identity. Please try again."


class EbaySellerAlreadyLinkedError(ConflictError):
    """This eBay seller account is already connected to a different workspace.

    Reported without confirming which workspace, or anything about it. Naming
    the other tenant — or varying the message depending on whether one exists —
    would turn this into an oracle for probing which sellers use the platform.
    """

    code = "ebay_seller_already_linked"
    message = "That eBay account is already connected to another DropPilot workspace."


__all__ = [
    "SERVICE_NAME",
    "EbayChallengeError",
    "EbayConsentDeniedError",
    "EbayError",
    "EbayIdentityUnavailableError",
    "EbayKeyUnavailableError",
    "EbayNotConfiguredError",
    "EbayNotificationRejectedError",
    "EbayOAuthStateError",
    "EbayPayloadTooLargeError",
    "EbaySellerAlreadyLinkedError",
    "EbaySignatureError",
    "EbayTokenExchangeError",
    "EbayTokenRevokedError",
]
