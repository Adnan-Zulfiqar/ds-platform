"""The broad per-identity request quota, applied to every request.

Counters live in Redis rather than process memory because the API runs as
multiple replicas — an in-process counter would let a client multiply its
allowance by the number of running containers.

The counting itself lives in :mod:`app.core.rate_limit`, shared with the
per-endpoint quotas in ``app.api.deps``. This module decides *who* is being
counted and *what is exempt*; it does not own a second implementation of the
window.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.context import get_request_id, get_tenant_id
from app.core.logging import get_logger
from app.core.rate_limit import limiter

logger = get_logger(__name__)

# Health and docs endpoints are exempt: probes must never be throttled, or a
# burst of traffic causes the orchestrator to declare the service dead and
# restart it, turning a slowdown into an outage.
#
# The AliExpress webhook is exempt for a different and less comfortable reason.
# Throttling it returns 429 to a delivery agent, which reads that as failure and
# redelivers on a schedule this application does not control — so a burst of
# legitimate notifications would be converted into a larger burst. Dropping a
# supplier's order update is worse than absorbing the traffic.
#
# The cost is real and is recorded in `TECHNICAL_DEBT.md`: this is an
# unauthenticated public POST endpoint with no throttle, so anyone who learns
# the URL can flood it. It is tolerable only while the handler is inert — it
# parses, logs and returns. Before the webhook does anything expensive
# (database writes, enqueuing tasks), it needs its own limiter, one that sheds
# load without returning a retry-provoking status.
# Shopify webhooks are HMAC-verified; returning 429 would provoke redelivery.
_SHOPIFY_WEBHOOK_PREFIX = "/api/v1/integrations/shopify/webhooks"

# The eBay compliance endpoint is throttled, but on its own budget rather than
# the general per-IP quota.
#
# Exempting it entirely — the choice made for the AliExpress webhook, and
# recorded in TECHNICAL_DEBT.md as a cost — is not acceptable here: this handler
# is not inert. It writes to the database and performs erasure, so an
# unthrottled public POST is a way to make the server do work.
#
# Applying the *general* quota is equally wrong. eBay delivers from its own
# infrastructure, so every notification shares a small set of source addresses
# and would be counted as one identity; a burst of legitimate deletion
# notifications would hit 429, and eBay reads 429 as failure and redelivers —
# converting a burst into a larger one, and eventually marking the endpoint
# down. eBay retries unacknowledged notifications for 24 hours before doing so.
#
# So: a dedicated, much larger budget on a separate counter. Enough that no
# plausible volume of real notifications is refused, small enough that a flood
# is still stopped rather than absorbed.
_EBAY_COMPLIANCE_PATH = "/api/v1/integrations/ebay/marketplace-account-deletion"
_EBAY_COMPLIANCE_LIMIT = 600
_EBAY_COMPLIANCE_WINDOW = 60

_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/integrations/aliexpress/webhook",
    }
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply a per-identity request quota."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._enabled = settings.security.rate_limit_enabled
        self._limit = settings.security.rate_limit_requests
        self._window = settings.security.rate_limit_window_seconds

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            not self._enabled
            or request.url.path in _EXEMPT_PATHS
            or request.url.path.startswith(_SHOPIFY_WEBHOOK_PREFIX)
        ):
            return await call_next(request)

        if request.url.path == _EBAY_COMPLIANCE_PATH:
            return await self._dispatch_ebay_compliance(request, call_next)

        identity = self._identity(request)
        allowed, remaining, retry_after = await self._consume(identity)

        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                identity=identity,
                path=request.url.path,
                limit=self._limit,
            )
            return self._too_many_requests(retry_after)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self._limit)
        response.headers["X-RateLimit-Remaining"] = str(max(remaining, 0))
        return response

    async def _dispatch_ebay_compliance(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Throttle the eBay compliance endpoint on its own counter.

        A separate Redis key, so eBay's traffic neither consumes nor is
        consumed by the general per-IP budget — a merchant browsing the app from
        the same egress address cannot throttle a deletion notification, and a
        flood here cannot lock that merchant out.
        """
        identity = f"ebay-compliance:{self._client_ip(request)}"
        decision = await limiter.consume(
            f"ratelimit:{identity}",
            limit=_EBAY_COMPLIANCE_LIMIT,
            window=_EBAY_COMPLIANCE_WINDOW,
        )
        if not decision.allowed:
            logger.warning(
                "rate_limit_exceeded",
                identity=identity,
                path=request.url.path,
                limit=_EBAY_COMPLIANCE_LIMIT,
            )
            return self._too_many_requests(decision.retry_after)
        return await call_next(request)

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _identity(self, request: Request) -> str:
        """Choose the key a quota is counted against.

        Tenant first: a customer's quota should be theirs regardless of how many
        machines they call from, and NAT means IP is a poor proxy for identity.
        Falls back to client IP for unauthenticated traffic, which is exactly
        the traffic — login, signup — most in need of throttling.
        """
        if tenant_id := get_tenant_id():
            return f"tenant:{tenant_id}"

        forwarded = request.headers.get("x-forwarded-for")
        client_ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "unknown")
        )
        return f"ip:{client_ip}"

    async def _consume(self, identity: str) -> tuple[bool, int, int]:
        """Count this request against the broad quota."""
        decision = await limiter.consume(
            f"ratelimit:{identity}", limit=self._limit, window=self._window
        )
        return decision.allowed, decision.remaining, decision.retry_after

    @staticmethod
    def _too_many_requests(retry_after: int) -> JSONResponse:
        """Build the 429 body.

        Shaped identically to every other error response, since a client should
        not need a second parser for throttling.
        """
        return JSONResponse(
            status_code=429,
            content={
                "code": "rate_limit_exceeded",
                "message": "Rate limit exceeded. Please retry later.",
                "details": [],
                "requestId": get_request_id(),
            },
            headers={"Retry-After": str(retry_after)},
        )


__all__ = ["RateLimitMiddleware"]
