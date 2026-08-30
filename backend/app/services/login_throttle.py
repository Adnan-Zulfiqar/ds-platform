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
import uuid

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import InfrastructureError, RateLimitExceededError
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


class StepUpUnavailableError(InfrastructureError):
    """The step-up limit could not be consulted, so the operation was refused.

    The login throttle fails *open* on a Redis outage, because locking every
    customer out of the product is worse than a window of unthrottled sign-in
    attempts. That trade does not carry over here. Step-up guards attaching a
    Google identity to an account and detaching one, and an unthrottled password
    oracle behind a stolen session is exactly what the limit exists to stop.
    Refusing costs a signed-in user one settings action until Redis returns.
    """

    code = "step_up_unavailable"


class StepUpThrottle:
    """Limits password re-entry for linking and unlinking an identity.

    **One counter for both operations.** Keying on the operation would let an
    attacker alternate link, unlink, link, unlink and get twice the guesses, so
    the key names the *user*, never what they were trying to do.

    **Counted before the password is checked, not after.** The login throttle
    reads a counter, verifies, then records a failure. That is right there and
    would not be enough here: twenty simultaneous requests all read the counter
    before any of them wrote, so twenty guesses would fit inside a limit of
    three. Reserving the attempt with `INCR` first makes the ceiling hard under
    concurrency, and it has the same useful side effect the login throttle has
    — a locked-out caller never reaches Argon2, so the limit protects CPU too.

    **Two dimensions, both incremented, either sufficient to refuse.** Per user,
    so a stolen session cannot grind one account's password; per address, so one
    client cannot work through many accounts. Both are hashed: Redis keys turn
    up in `MONITOR`, in slow-log entries and in support dumps, and an address
    sitting in a key space is a personal-data leak waiting to be exported.

    **Nothing here is an enumeration oracle.** A refusal reads the same whether
    the password was right, wrong, or never examined, and the user dimension is
    keyed by tenant and id rather than by email, so watching it tells a caller
    nothing about any other workspace.

    `INCR`, `EXPIRE`, `TTL` and `DEL` are all long-standing Redis commands;
    nothing here needs anything the deployed 3.0.504 lacks.
    """

    def __init__(self) -> None:
        self._security = settings.security

    def _keys(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None, client_ip: str | None
    ) -> list[str]:
        """The dimensions this attempt counts against.

        The tenant is folded into the user digest rather than given a dimension
        of its own. A per-tenant counter would let one member of a workspace
        lock out every colleague — a denial of service dressed as a control.
        Including it in the hash makes the scoping explicit and collision-proof
        without creating that shared fate.
        """
        scope = f"{tenant_id or 'none'}:{user_id}"
        keys = [f"stepup:user:{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:32]}"]
        if client_ip:
            digest = hashlib.sha256(client_ip.strip().encode("utf-8")).hexdigest()[:32]
            keys.append(f"stepup:ip:{digest}")
        return keys

    async def reserve(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None, client_ip: str | None
    ) -> None:
        """Count this attempt and refuse if either dimension is exhausted.

        Call before verifying the password. Raises
        :class:`RateLimitExceededError` when a ceiling is reached and
        :class:`StepUpUnavailableError` when Redis cannot be reached.

        Every dimension is incremented before any is judged, so an attempt that
        trips the per-user ceiling still counts against the address it came
        from. Otherwise an attacker learns which accounts are already locked and
        moves to the next one for free.
        """
        if not self._security.rate_limit_enabled:
            return

        keys = self._keys(user_id=user_id, tenant_id=tenant_id, client_ip=client_ip)
        maximum = self._security.step_up_max_attempts

        try:
            client = get_redis(RedisPurpose.RATE_LIMIT)

            counts: list[int] = []
            for key in keys:
                count = int(await client.incr(key))
                if count == 1:
                    await client.expire(key, self._security.step_up_attempt_window_seconds)
                elif count > maximum:
                    # Extended on every further attempt, so a lockout cannot be
                    # waited out at the rate it was earned.
                    await client.expire(key, self._security.step_up_lockout_seconds)
                counts.append(count)

            exhausted = [key for key, count in zip(keys, counts, strict=True) if count > maximum]
        except RedisError as exc:
            # Fails closed — see `StepUpUnavailableError`.
            logger.error("step_up_throttle_backend_unavailable", error=str(exc))
            raise StepUpUnavailableError(
                "That request could not be completed just now. Please try again."
            ) from exc

        if exhausted:
            key = exhausted[0]
            try:
                ttl = int(await get_redis(RedisPurpose.RATE_LIMIT).ttl(key))
            except RedisError:
                ttl = 0
            logger.warning("step_up_throttled", dimension=key.split(":")[1])
            raise RateLimitExceededError(
                "Too many attempts. Please try again later.",
                retry_after_seconds=(ttl if ttl > 0 else self._security.step_up_lockout_seconds),
            )

    async def clear(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None, client_ip: str | None
    ) -> None:
        """Reset both counters after a correct password.

        Someone who mistypes once and then gets it right starts fresh rather
        than carrying that failure toward a lockout for the next fifteen
        minutes. A clear that cannot reach Redis is logged and ignored: the
        counters expire on their own, and failing the operation *after* the
        password was accepted would punish the person who got it right.
        """
        if not self._security.rate_limit_enabled:
            return

        try:
            client = get_redis(RedisPurpose.RATE_LIMIT)
            await client.delete(
                *self._keys(user_id=user_id, tenant_id=tenant_id, client_ip=client_ip)
            )
        except RedisError as exc:
            logger.error("step_up_throttle_clear_failed", error=str(exc))


__all__ = ["LoginThrottle", "StepUpThrottle", "StepUpUnavailableError"]
