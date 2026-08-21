"""The fixed-window rate limiter, shared by every caller that needs one.

Extracted from ``app.middleware.rate_limit`` when a second caller appeared.
It has exactly one implementation on purpose: the Lua script, the circuit
breaker and the fail-open policy are the parts that are easy to get subtly
wrong, and a second copy would drift from this one at the worst moment.

Two callers today:

* the middleware, which applies a broad per-identity quota to every request;
* :class:`app.api.deps.RateLimited`, which applies a *tighter*, named quota to
  the handful of endpoints that are individually expensive.

The named quota is not a replacement for the global one — a request passes
through both.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis

logger = get_logger(__name__)

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


@dataclass(frozen=True, slots=True)
class LimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class FixedWindowLimiter:
    """One counter per identity per window, in Redis.

    Fixed window rather than a sliding log: a sliding window is fairer at the
    boundary but stores an entry per request, where this stores one integer per
    identity and permits at most double the nominal rate across a boundary.
    That trade is right for a limiter whose job is to blunt abuse rather than
    meter billing.

    **Fails open.** A Redis outage must not take the API down with it. The
    consequence is real and deliberate: while Redis is down there is no quota
    enforcement at all, which is why Redis availability needs its own alerting.
    """

    def __init__(self) -> None:
        self._script_sha: str | None = None
        self._failure_threshold = settings.redis.circuit_breaker_threshold
        self._cooldown = settings.redis.circuit_breaker_cooldown_seconds
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _circuit_is_open(self) -> bool:
        """Whether Redis calls are currently short-circuited.

        Monotonic clock: the wall clock can jump backwards on an NTP
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

    async def consume(self, key: str, *, limit: int, window: int) -> LimitDecision:
        """Count one request against ``key``."""
        # Short-circuit while the breaker is open. Without this, every request
        # pays a failed connection attempt for the duration of an outage.
        if self._circuit_is_open():
            return LimitDecision(True, limit, limit, 0)

        client = get_redis(RedisPurpose.RATE_LIMIT)
        try:
            if self._script_sha is None:
                self._script_sha = await client.script_load(_RATE_LIMIT_SCRIPT)
            count, ttl = await client.evalsha(self._script_sha, 1, key, str(window))
        except RedisError as exc:
            # The cached SHA is discarded too: a Redis restart flushes the
            # script cache, and reusing a stale SHA would fail forever with
            # NOSCRIPT once the server came back.
            self._script_sha = None
            self._record_failure(str(exc))
            return LimitDecision(True, limit, limit, 0)

        self._record_success()
        count = int(count)
        ttl = int(ttl) if int(ttl) > 0 else window
        return LimitDecision(count <= limit, limit, limit - count, ttl)


#: One limiter per process. Its state is a Redis script cache and a circuit
#: breaker, both of which are per-process facts, so sharing it is correct
#: rather than merely convenient.
limiter = FixedWindowLimiter()


__all__ = ["FixedWindowLimiter", "LimitDecision", "limiter"]
