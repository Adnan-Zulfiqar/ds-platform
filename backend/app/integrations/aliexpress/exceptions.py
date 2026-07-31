"""AliExpress integration errors.

All inherit from :class:`ExternalServiceError`, which Phase 0 defined for
exactly this purpose. The global handler therefore already knows how to render
them, and none of the HTTP layer needed changing to accommodate this phase.

The distinction that matters throughout: **which failures are worth retrying.**
A timeout is; a rejected app secret is not, and retrying it three times only
delays telling the user something they must fix. Every exception here declares
``retryable`` so callers do not have to guess from a status code.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ExternalServiceError

SERVICE_NAME = "aliexpress"


class AliExpressError(ExternalServiceError):
    """Base for every AliExpress failure."""

    code = "aliexpress_error"
    message = "The AliExpress integration encountered an error."

    #: Whether retrying the identical request could plausibly succeed.
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        upstream_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {**(details or {})}
        if upstream_code:
            merged["upstream_code"] = upstream_code
        super().__init__(message, service=SERVICE_NAME, details=merged)
        self.upstream_code = upstream_code


class AliExpressTimeoutError(AliExpressError):
    """The request exceeded its timeout.

    Retryable: the call may simply have been slow, and the operation may not
    have been processed at all.
    """

    code = "aliexpress_timeout"
    message = "AliExpress did not respond in time."
    retryable = True


class AliExpressUnavailableError(AliExpressError):
    """A 5xx, or the connection could not be established."""

    code = "aliexpress_unavailable"
    message = "AliExpress is temporarily unavailable."
    retryable = True


class AliExpressRateLimitError(AliExpressError):
    """Quota exhausted, ours or theirs.

    Retryable, but only after waiting. ``retry_after_seconds`` carries the delay
    when the response or our own limiter supplies one; retrying immediately
    would deepen the problem.
    """

    code = "aliexpress_rate_limited"
    message = "The AliExpress request quota has been exhausted. Please retry later."
    retryable = True

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        upstream_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, upstream_code=upstream_code, details=details)
        self.retry_after_seconds = retry_after_seconds


class AliExpressAuthError(AliExpressError):
    """Credentials were rejected.

    **Not retryable.** A wrong app secret is wrong every time; retrying wastes
    quota and delays telling the user what they need to fix.
    """

    code = "aliexpress_auth_failed"
    message = "AliExpress rejected the credentials for this connection."
    status_code = 400


class AliExpressTokenExpiredError(AliExpressAuthError):
    """The access token has expired and needs refreshing.

    Separate from a general auth failure because the response differs: this one
    is recoverable without the user doing anything, by exchanging the refresh
    token.
    """

    code = "aliexpress_token_expired"
    message = "The AliExpress access token has expired."


class AliExpressNotConnectedError(AliExpressError):
    """No usable connection exists for this tenant.

    A 409 rather than a 404: the tenant exists and the endpoint is correct, but
    the operation cannot proceed in the current state.
    """

    code = "aliexpress_not_connected"
    message = "No AliExpress account is connected for this workspace."
    status_code = 409


class AliExpressResponseError(AliExpressError):
    """A well-formed HTTP response the client could not interpret.

    Covers malformed JSON and a documented error payload with an unrecognised
    code. Not retryable: the same request produces the same unusable answer.
    """

    code = "aliexpress_invalid_response"
    message = "AliExpress returned an unexpected response."


class AliExpressOAuthStateError(AliExpressError):
    """The OAuth ``state`` parameter was missing, unknown, or expired.

    Treated as a security event rather than a validation slip. A callback with
    no matching state is either a stale browser tab or a forged request trying
    to bind an attacker's supplier account to someone else's workspace.
    """

    code = "aliexpress_invalid_state"
    message = "This authorization request is no longer valid. Please start again."
    status_code = 400


__all__ = [
    "SERVICE_NAME",
    "AliExpressAuthError",
    "AliExpressError",
    "AliExpressNotConnectedError",
    "AliExpressOAuthStateError",
    "AliExpressRateLimitError",
    "AliExpressResponseError",
    "AliExpressTimeoutError",
    "AliExpressTokenExpiredError",
    "AliExpressUnavailableError",
]
