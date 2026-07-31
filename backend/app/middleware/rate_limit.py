"""Distributed rate limiting.

Counters live in Redis rather than process memory because the API runs as
multiple replicas — an in-process counter would let a client multiply its
allowance by the number of running containers.

Implemented as a fixed window: one counter per identity per window, incremented
atomically, expiring when the window closes. A sliding-window log is fairer at
the boundary but stores one entry per request; a fixed window stores one integer
per identity and permits at most double the nominal rate across a boundary. That
trade is right at this layer, whose job is to blunt abuse rather than to meter
billing precisely.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.context import get_request_id, get_tenant_id
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis

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

# Atomic increment-and-expire.
#
# Must be a single Lua script rather than INCR followed by EXPIRE: between two
# separate round trips the process can die, leaving a counter with no TTL that
# never resets and locks the client out permanently.
_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply a per-identity request quota."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._enabled = settings.security.rate_limit_enabled
        self._limit = settings.security.rate_limit_requests
        self._window = settings.security.rate_limit_window_seconds
        self._script_sha: str | None = None

        # Circuit breaker state, per process.
        self._failure_threshold = settings.redis.circuit_breaker_threshold
        self._cooldown = settings.redis.circuit_breaker_cooldown_seconds
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self._enabled or request.url.path in _EXEMPT_PATHS:
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

    def _circuit_is_open(self) -> bool:
        """Return whether Redis calls are currently short-circuited.

        Uses a monotonic clock: the wall clock can jump backwards on an NTP
        correction, which would leave the breaker stuck open.
        """
        return time.monotonic() < self._circuit_open_until

    def _record_failure(self, error: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold and not self._circuit_is_open():
            self._circuit_open_until = time.monotonic() + self._cooldown
            logger.error(
                "rate_limit_circuit_opened",
                error=error,
                consecutive_failures=self._consecutive_failures,
                cooldown_seconds=self._cooldown,
            )
        else:
            logger.warning("rate_limit_backend_error", error=error)

    def _record_success(self) -> None:
        if self._consecutive_failures:
            logger.info("rate_limit_circuit_closed")
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    async def _consume(self, identity: str) -> tuple[bool, int, int]:
        """Increment the counter. Returns (allowed, remaining, retry_after).

        Fails open on any Redis problem. A Redis outage must not take the API
        down with it — the alternative is that a cache failure becomes a total
        outage. This is a deliberate availability-over-enforcement trade, and it
        means Redis availability needs its own alerting, since while it is down
        the platform has no quota enforcement at all.
        """
        # Short-circuit while the breaker is open. Without this, every single
        # request pays a failed connection attempt during an outage.
        if self._circuit_is_open():
            return True, self._limit, 0

        client = get_redis(RedisPurpose.RATE_LIMIT)
        key = f"ratelimit:{identity}"

        try:
            if self._script_sha is None:
                self._script_sha = await client.script_load(_RATE_LIMIT_SCRIPT)
            count, ttl = await client.evalsha(self._script_sha, 1, key, str(self._window))
        except RedisError as exc:
            # The cached script SHA is discarded too: a Redis restart flushes
            # the script cache, and reusing a stale SHA would fail forever with
            # NOSCRIPT once the server came back.
            self._script_sha = None
            self._record_failure(str(exc))
            return True, self._limit, 0

        self._record_success()
        count = int(count)
        ttl = int(ttl) if int(ttl) > 0 else self._window
        return count <= self._limit, self._limit - count, ttl

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
