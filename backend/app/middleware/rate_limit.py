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
