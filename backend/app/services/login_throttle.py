"""Login attempt throttling.

Separate from the general API rate limiter, and far stricter. ``/auth/login`` is
unauthenticated, accepts guessable input, and is the single highest-value target
in the application — credential stuffing against it is constant background noise
on the public internet.

**Keyed on email *and* client IP, counted independently.** Either alone is
insufficient:

* IP alone lets an attacker spread one password across thousands of accounts
  from one address without ever tripping a per-account limit, and it punishes
  every user behind a shared NAT when one of them mistypes.
* Email alone lets an attacker lock a known user out of their own account by
  deliberately failing their login — a denial-of-service disguised as a security
  control.

Requiring both to stay under the threshold blocks the credential-stuffing
pattern while keeping account lockout bounded.

**Counters are cleared on success**, so a user who mistypes twice and then gets
it right starts fresh rather than carrying failures towards a lockout.
"""

from __future__ import annotations

import hashlib

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis

logger = get_logger(__name__)


def _identity_key(kind: str, value: str) -> str:
    """Build a Redis key for one throttle dimension.

    The email is hashed rather than stored in plain text. Redis keys turn up in
    ``MONITOR`` output, in slow-log entries, and in support dumps, and a key
    space full of customer email addresses is a personal-data leak waiting to
    be exported. A hash still counts correctly.
    """
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]
    return f"login:{kind}:{digest}"


class LoginThrottle:
    """Tracks and limits failed login attempts."""

    def __init__(self) -> None:
        self._security = settings.security

    async def check(self, *, email: str, client_ip: str | None) -> None:
        """Raise :class:`RateLimitExceededError` if either counter is exhausted.

        Called *before* the password is verified, so a locked-out caller never
        reaches the expensive Argon2 verification — which also means the lockout
        protects CPU as well as accounts.

        **Fails open** on a Redis error, consistent with the general rate
        limiter: an infrastructure outage must not lock every customer out of
        their own product. The trade is explicit and means Redis availability
        needs its own alerting.
        """
        if not self._security.rate_limit_enabled:
            return

        try:
            client = get_redis(RedisPurpose.RATE_LIMIT)
            keys = [_identity_key("email", email)]
            if client_ip:
                keys.append(_identity_key("ip", client_ip))

            for key in keys:
                raw = await client.get(key)
                if raw is not None and int(raw) >= self._security.login_max_attempts:
                    ttl = await client.ttl(key)
                    retry_after = ttl if ttl > 0 else self._security.login_lockout_seconds
                    logger.warning("login_throttled", dimension=key.split(":")[1])
                    raise RateLimitExceededError(
                        "Too many failed sign-in attempts. Please try again later.",
                        retry_after_seconds=retry_after,
                    )
        except RedisError as exc:
            logger.error("login_throttle_backend_unavailable", error=str(exc))

    async def record_failure(self, *, email: str, client_ip: str | None) -> None:
        """Increment both counters after a failed attempt.

        The first failure sets the expiry. Once the threshold is reached the
        window is extended to the full lockout duration, so an attacker cannot
        simply wait out a short window and resume at the same rate.
        """
        if not self._security.rate_limit_enabled:
            return

        try:
            client = get_redis(RedisPurpose.RATE_LIMIT)
            keys = [_identity_key("email", email)]
            if client_ip:
                keys.append(_identity_key("ip", client_ip))

            for key in keys:
                count = await client.incr(key)
                if count == 1:
                    await client.expire(key, self._security.login_attempt_window_seconds)
                elif count >= self._security.login_max_attempts:
                    await client.expire(key, self._security.login_lockout_seconds)
        except RedisError as exc:
            logger.error("login_throttle_record_failed", error=str(exc))

    async def clear(self, *, email: str, client_ip: str | None) -> None:
        """Reset both counters after a successful sign-in."""
        if not self._security.rate_limit_enabled:
            return

        try:
            client = get_redis(RedisPurpose.RATE_LIMIT)
            keys = [_identity_key("email", email)]
            if client_ip:
                keys.append(_identity_key("ip", client_ip))
            await client.delete(*keys)
        except RedisError as exc:
            logger.error("login_throttle_clear_failed", error=str(exc))


__all__ = ["LoginThrottle"]
