"""Outbound rate limiting for third-party APIs.

The inbound limiter in ``app.middleware.rate_limit`` protects this platform from
its callers. This one protects *us from ourselves*: it caps the requests the
platform makes to a supplier API.

The distinction matters because the consequences differ. Exceeding a supplier's
quota does not just fail the current request — providers commonly throttle or
suspend an application key after sustained abuse, which takes down the
integration for **every tenant** using it. Staying under the limit locally is
cheaper than discovering the ceiling by hitting it.

**Counted per tenant and provider.** A global counter would let one busy tenant
consume the whole allowance and starve everyone else, which is the multi-tenant
version of the same problem.

Unlike the inbound limiter, this one **fails closed**: if Redis is unavailable
the request is refused rather than allowed. Failing open there protects
availability of our own API; failing open here would remove the only guard on
an external quota at exactly the moment we cannot measure it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis

logger = get_logger(__name__)

# Atomic increment-and-expire, identical in shape to the inbound limiter's.
#
# A single script rather than INCR followed by EXPIRE: between two round trips
# the process can die, leaving a counter with no TTL that never resets and locks
# the integration out permanently.
_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a quota check."""

    allowed: bool
    remaining: int
    retry_after_seconds: int


class OutboundRateLimiter:
    """Fixed-window quota for calls to one provider on behalf of one tenant."""

    def __init__(
        self,
        *,
        provider: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        self.provider = provider
        self.limit = limit
        self.window_seconds = window_seconds
        self._script_sha: str | None = None

    def _key(self, tenant_id: str) -> str:
        return f"outbound:{self.provider}:{tenant_id}"

    async def acquire(self, tenant_id: str) -> RateLimitDecision:
        """Consume one unit of quota.

        Raises nothing — the caller decides what a refusal means, because the
        right response differs between a user-facing request (report it) and a
        background sync (defer it).
        """
        client = get_redis(RedisPurpose.RATE_LIMIT)
        key = self._key(tenant_id)

        try:
            if self._script_sha is None:
                self._script_sha = await client.script_load(_RATE_LIMIT_SCRIPT)
            count, ttl = await client.evalsha(self._script_sha, 1, key, str(self.window_seconds))
        except RedisError as exc:
            # A Redis restart flushes the script cache, so a cached SHA would
            # fail forever with NOSCRIPT once it came back.
            self._script_sha = None
            logger.error(
                "outbound_rate_limiter_unavailable",
                provider=self.provider,
                error=str(exc),
            )
            # Fails closed — see the module docstring.
            return RateLimitDecision(
                allowed=False, remaining=0, retry_after_seconds=self.window_seconds
            )

        count = int(count)
        ttl = int(ttl) if int(ttl) > 0 else self.window_seconds
        allowed = count <= self.limit

        if not allowed:
            logger.warning(
                "outbound_rate_limit_exceeded",
                provider=self.provider,
                tenant_id=tenant_id,
                limit=self.limit,
            )

        return RateLimitDecision(
            allowed=allowed,
            remaining=max(self.limit - count, 0),
            retry_after_seconds=ttl,
        )

    async def current_usage(self, tenant_id: str) -> int:
        """Requests used in the current window. For diagnostics and display."""
        try:
            raw = await get_redis(RedisPurpose.RATE_LIMIT).get(self._key(tenant_id))
        except RedisError:
            return 0
        return int(raw) if raw else 0


def compute_backoff(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
) -> float:
    """Exponential backoff with full jitter.

    ``attempt`` is zero-based, so the first retry waits roughly ``base_seconds``.

    **The jitter is not decoration.** When a provider has an outage, every
    pending request fails at once; without jitter they all retry at the same
    instant and arrive as a synchronised wave, which is precisely what stops the
    provider recovering. Randomising across the whole interval spreads them out.

    Uses the standard ``random`` module rather than ``secrets``: this is
    scheduling, not a security decision, and nothing is predicted from it.
    """
    import random

    ceiling = min(base_seconds * (2**attempt), max_seconds)
    return random.uniform(0, ceiling)  # noqa: S311


def seconds_until(deadline: float) -> float:
    """Seconds remaining until a monotonic deadline, never negative."""
    return max(deadline - time.monotonic(), 0.0)


__all__ = [
    "OutboundRateLimiter",
    "RateLimitDecision",
    "compute_backoff",
    "seconds_until",
]
