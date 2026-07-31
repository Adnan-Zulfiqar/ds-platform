"""AliExpress HTTP client.

The only place in the codebase that talks to AliExpress over the network. It
owns timeouts, retries, rate limiting, and the translation of every failure mode
into a typed exception from ``.exceptions``.

Callers therefore never see an ``httpx`` object, a status code, or an AliExpress
error string. That boundary is what lets the rest of the application be tested
without a network and reasoned about without knowing AliExpress's error
vocabulary.

**Three details that are easy to get wrong and expensive to get wrong:**

* **AliExpress reports failure inside HTTP 200.** The body is inspected on every
  response regardless of status. Trusting the status code alone means treating
  a rejected signature as a success and storing a token that does not exist.
* **Only transient failures are retried.** Retrying a rejected app secret three
  times wastes quota and delays telling the user what they must fix.
* **Credentials never reach a log.** The signed parameter set contains the app
  key and signature; the request is logged by endpoint and outcome only.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.aliexpress.auth import build_signed_params, signing_path_for
from app.integrations.aliexpress.exceptions import (
    AliExpressAuthError,
    AliExpressError,
    AliExpressRateLimitError,
    AliExpressResponseError,
    AliExpressTimeoutError,
    AliExpressTokenExpiredError,
    AliExpressUnavailableError,
)
from app.integrations.aliexpress.schemas import AliExpressErrorResponse
from app.integrations.rate_limiter import OutboundRateLimiter, compute_backoff

logger = get_logger(__name__)

#: Upstream error codes that mean "the token is dead, refresh it".
#:
#: Matched as a prefix set because AliExpress appends sub-codes. Anything
#: unrecognised falls through to a generic auth error rather than being assumed
#: refreshable — retrying a refresh against a revoked grant achieves nothing.
_TOKEN_EXPIRED_CODES = frozenset(
    {"27", "26", "InvalidAccessToken", "AccessTokenExpired", "IllegalAccessToken"}
)

#: Codes that mean the caller is over quota.
_RATE_LIMIT_CODES = frozenset({"7", "15", "ApiCallLimit", "AppCallLimit", "ServiceCallLimit"})

#: Codes that indicate bad credentials rather than an expired token.
_AUTH_ERROR_CODES = frozenset(
    {"11", "12", "25", "InvalidSignature", "InvalidAppKey", "IncompleteSignature"}
)


class AliExpressClient:
    """Performs signed, rate-limited, retrying calls to AliExpress.

    One instance per operation is fine — ``httpx.AsyncClient`` is created inside
    the context manager, so there is no connection pool to share. When call
    volume justifies pooling, the client becomes a constructor argument; nothing
    else changes.
    """

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        tenant_id: str,
        access_token: str | None = None,
    ) -> None:
        self._app_key = app_key
        # Held only to sign requests. Never logged, never returned, never placed
        # in a parameter that is transmitted.
        self._app_secret = app_secret
        self._access_token = access_token
        self._tenant_id = tenant_id
        self._config = settings.aliexpress

        self._rate_limiter = OutboundRateLimiter(
            provider="aliexpress",
            limit=self._config.rate_limit_requests,
            window_seconds=self._config.rate_limit_window_seconds,
        )

    # -- Public API ---------------------------------------------------------

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        api_path: str = "",
        require_token: bool = True,
    ) -> dict[str, Any]:
        """Invoke an AliExpress API method and return its payload.

        ``method`` is the API name (for example ``aliexpress.ds.product.get``).
        Raises a typed :class:`AliExpressError` on any failure.
        """
        payload: dict[str, Any] = {"method": method, **(params or {})}

        if require_token:
            if not self._access_token:
                raise AliExpressAuthError("No access token is available for this call.")
            payload["access_token"] = self._access_token

        signed = build_signed_params(
            payload,
            app_key=self._app_key,
            app_secret=self._app_secret,
            api_path=api_path,
        )

        return await self._request_with_retries(self._config.api_base_url, signed, operation=method)

    async def exchange_token(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """Perform a token create or refresh call.

        Separate from :meth:`call` because these endpoints take no
        ``access_token`` — obtaining one is the point — and live on different
        paths from the API gateway.

        **The signature must include the API path for these endpoints.** They
        are REST-style (``/rest/auth/token/create``), and REST-style requests
        prefix the base string with the path minus the ``/rest`` routing
        segment. An earlier version signed without it, producing a signature the
        gateway cannot reproduce — surfacing as an opaque "invalid signature"
        rejection at the exact moment a user finishes authorising, with nothing
        in the message to indicate why.
        """
        signed = build_signed_params(
            params,
            app_key=self._app_key,
            app_secret=self._app_secret,
            api_path=signing_path_for(url),
        )
        return await self._request_with_retries(url, signed, operation="token_exchange")

    # -- Transport ----------------------------------------------------------

    async def _request_with_retries(
        self, url: str, params: dict[str, Any], *, operation: str
    ) -> dict[str, Any]:
        """Send the request, retrying only failures that could resolve themselves."""
        decision = await self._rate_limiter.acquire(self._tenant_id)
        if not decision.allowed:
            raise AliExpressRateLimitError(
                "The AliExpress request quota for this workspace is exhausted.",
                retry_after_seconds=decision.retry_after_seconds,
            )

        last_error: AliExpressError | None = None

        # One initial attempt plus `max_retries` retries.
        for attempt in range(self._config.max_retries + 1):
            try:
                return await self._request_once(url, params, operation=operation)
            except AliExpressError as exc:
                last_error = exc

                if not exc.retryable or attempt == self._config.max_retries:
                    raise

                delay = compute_backoff(
                    attempt,
                    base_seconds=self._config.retry_backoff_seconds,
                    max_seconds=self._config.retry_backoff_max_seconds,
                )
                # A rate limit response carries its own wait, which is
                # authoritative — backing off for less simply fails again.
                if isinstance(exc, AliExpressRateLimitError) and exc.retry_after_seconds:
                    delay = max(delay, float(exc.retry_after_seconds))

                logger.warning(
                    "aliexpress_retrying",
                    operation=operation,
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 2),
                    reason=exc.code,
                )
                await asyncio.sleep(delay)

        # Unreachable: the loop either returns or raises. Present so the
        # function has no implicit None path.
        raise last_error or AliExpressError("The AliExpress request failed.")

    async def _request_once(
        self, url: str, params: dict[str, Any], *, operation: str
    ) -> dict[str, Any]:
        timeout = httpx.Timeout(
            self._config.request_timeout_seconds,
            connect=self._config.connect_timeout_seconds,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, data=params)
        except httpx.TimeoutException as exc:
            logger.warning("aliexpress_timeout", operation=operation)
            raise AliExpressTimeoutError() from exc
        except httpx.HTTPError as exc:
            # Connection refused, DNS failure, TLS error. The message can
            # contain the URL but never the payload, so it is safe to log.
            logger.warning(
                "aliexpress_transport_error", operation=operation, error=type(exc).__name__
            )
            raise AliExpressUnavailableError() from exc

        return self._interpret(response, operation=operation)

    def _interpret(self, response: httpx.Response, *, operation: str) -> dict[str, Any]:
        """Turn a response into a payload or a typed exception.

        The body is parsed before the status is considered, because AliExpress
        routinely returns a documented error inside HTTP 200.
        """
        if response.status_code >= 500:
            logger.warning(
                "aliexpress_server_error",
                operation=operation,
                status_code=response.status_code,
            )
            raise AliExpressUnavailableError()

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise AliExpressRateLimitError(
                retry_after_seconds=int(retry_after) if retry_after else None
            )

        try:
            body = response.json()
        except ValueError as exc:
            # Never log the body: an error page can echo request parameters,
            # which include the app key and signature.
            logger.error(
                "aliexpress_unparseable_response",
                operation=operation,
                status_code=response.status_code,
            )
            raise AliExpressResponseError() from exc

        if not isinstance(body, dict):
            raise AliExpressResponseError("AliExpress returned an unexpected payload.")

        # The gateway wraps documented failures in an `error_response` envelope:
        # {"error_response": {"type": "ISV", "code": "MissingParameter", ...}}.
        # Found by live verification in Phase 5 — validating only the top level
        # let an ApiCallLimit response through as a *success*, silently skipping
        # the retry the rate-limit mapping exists to trigger. The envelope is
        # unwrapped before interpretation; a flat error body (older gateways)
        # still works because the fallback is the body itself.
        error_body = body.get("error_response")
        error = AliExpressErrorResponse.model_validate(
            error_body if isinstance(error_body, dict) else body
        )
        if error.is_error:
            raise self._map_error(error, operation=operation)

        if response.status_code >= 400:
            # A 4xx with no recognisable error body. Not retryable — the same
            # request produces the same rejection.
            logger.warning(
                "aliexpress_client_error",
                operation=operation,
                status_code=response.status_code,
            )
            raise AliExpressResponseError(
                f"AliExpress rejected the request with status {response.status_code}."
            )

        return body

    def _map_error(self, error: AliExpressErrorResponse, *, operation: str) -> AliExpressError:
        """Map a documented error payload to a typed exception.

        Sub-codes are checked alongside top-level codes because AliExpress
        reports the specific cause there while the top-level code stays generic.
        """
        codes = {str(error.code or ""), str(error.sub_code or "")}
        # The upstream message is safe to surface: it describes the request, not
        # the credentials.
        message = error.sub_message or error.message

        logger.warning(
            "aliexpress_api_error",
            operation=operation,
            upstream_code=error.code,
            upstream_sub_code=error.sub_code,
            upstream_request_id=error.request_id,
        )

        if codes & _TOKEN_EXPIRED_CODES:
            return AliExpressTokenExpiredError(upstream_code=error.code)
        if codes & _RATE_LIMIT_CODES:
            return AliExpressRateLimitError(upstream_code=error.code)
        if codes & _AUTH_ERROR_CODES:
            return AliExpressAuthError(upstream_code=error.code)

        return AliExpressResponseError(
            message or "AliExpress returned an error.", upstream_code=error.code
        )


__all__ = ["AliExpressClient"]
