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


#: One attempt, counted and bounded, in a single round trip.
#:
#: The three commands have to be indivisible. The previous version sent `INCR`
#: and then `EXPIRE` as separate calls, so a process or network failure between
#: them left a counter with no expiry — an immortal key that locks a user out of
#: linking or unlinking until somebody deletes it by hand. A script runs as one
#: unit inside Redis: either the counter and its expiry both exist afterwards,
#: or neither does.
#:
#: `EVAL` is Redis 2.6 and works on the deployed 3.0.504 (verified against a
#: real 3.0.504 instance, not assumed). The script touches one key, so it is
#: also safe under a future cluster.
#:
#: Two deliberate properties, both of which the arithmetic has to get right:
#:
#: * **The expiry is only ever assigned when there is none, or once at the
#:   moment the ceiling is crossed.** It is never refreshed on later attempts.
#:   Refreshing would mean an attacker holding a stolen session could keep a
#:   lockout open indefinitely by continuing to guess — a denial of service
#:   against the real account holder, handed over by the control meant to
#:   protect them.
#: * **A key found without an expiry is repaired**, which is what heals a
#:   counter left behind by the previous non-atomic version.
_RESERVE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
local window = tonumber(ARGV[1])
local lockout = tonumber(ARGV[2])
local maximum = tonumber(ARGV[3])

if ttl < 0 then
  -- A fresh counter, or one left without an expiry by an older version.
  redis.call('EXPIRE', KEYS[1], window)
  ttl = window
end

if count == maximum + 1 then
  -- The single attempt that crosses the ceiling extends the key to the lockout
  -- duration. Later attempts deliberately do not, so the lockout is bounded at
  -- exactly that long from the moment it was earned.
  redis.call('EXPIRE', KEYS[1], lockout)
  ttl = lockout
end

return {count, ttl}
"""


class StepUpThrottle:
    """Limits password re-entry for linking and unlinking an identity.

    **One authority for both operations.** Keying on the operation would let an
    attacker alternate link, unlink, link, unlink and get twice the guesses, so
    the key names the *user*, never what they were trying to do.

    **Counted before the password is checked, not after.** The login throttle
    reads a counter, verifies, then records a failure. That is right there and
    would not be enough here: twenty simultaneous requests all read the counter
    before any of them wrote, so twenty guesses would fit inside a limit of
    three. Reserving the attempt first makes the ceiling hard under concurrency,
    and it has the same useful side effect the login throttle has — a
    locked-out caller never reaches Argon2, so the limit protects CPU too.

    **Two dimensions, counted and bounded independently.**

    * *Per user*, scoped by tenant: this is the control that bounds password
      guessing against any one account, and nothing another account does can
      reset it.
    * *Per address*: the aggregate-abuse control, so one client cannot work
      through many accounts. Its ceiling is deliberately looser
      (`step_up_max_attempts_per_ip`), because it is genuinely shared — see
      `clear_user_attempts` for what that costs and why it is the right trade.

    Both are hashed. Redis keys turn up in `MONITOR`, in slow-log entries and in
    support dumps, and an address sitting in a key space is a personal-data leak
    waiting to be exported. Nothing here logs a raw address, user or tenant
    either.

    **Nothing here is an enumeration oracle.** A refusal reads the same whether
    the password was right, wrong, or never examined.

    `INCR`, `EXPIRE`, `TTL`, `DEL` and `EVAL` are all long-standing Redis
    commands; nothing here needs anything the deployed 3.0.504 lacks, and
    nothing here uses `GETDEL`, `UNLINK`, `KEYS`, `FLUSHDB` or `FLUSHALL`.
    """

    def __init__(self) -> None:
        self._security = settings.security

    def user_key(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None) -> str:
        """The per-account dimension.

        The tenant is folded into the digest rather than given a dimension of
        its own. A per-tenant counter would let one member of a workspace lock
        out every colleague — a denial of service dressed as a control.
        """
        scope = f"{tenant_id or 'none'}:{user_id}"
        return f"stepup:user:{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:32]}"

    def address_key(self, client_ip: str) -> str:
        """The shared dimension. Hashed, so a Redis dump is not a visitor log."""
        return f"stepup:ip:{hashlib.sha256(client_ip.strip().encode('utf-8')).hexdigest()[:32]}"

    def _dimensions(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None, client_ip: str | None
    ) -> list[tuple[str, str, int]]:
        """`(label, key, ceiling)` for each dimension this attempt counts against."""
        dimensions = [
            (
                "user",
                self.user_key(user_id=user_id, tenant_id=tenant_id),
                self._security.step_up_max_attempts,
            )
        ]
        if client_ip:
            dimensions.append(
                ("ip", self.address_key(client_ip), self._security.step_up_max_attempts_per_ip)
            )
        return dimensions

    async def reserve(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None, client_ip: str | None
    ) -> None:
        """Count this attempt and refuse if either dimension is exhausted.

        Call before verifying the password. Raises
        :class:`RateLimitExceededError` when a ceiling is reached and
        :class:`StepUpUnavailableError` when Redis cannot be reached.

        Every dimension is counted before any is judged, so an attempt that
        trips the per-user ceiling still counts against the address it came
        from. Otherwise an attacker learns which accounts are already locked and
        moves to the next one for free.
        """
        if not self._security.rate_limit_enabled:
            return

        dimensions = self._dimensions(user_id=user_id, tenant_id=tenant_id, client_ip=client_ip)

        try:
            client = get_redis(RedisPurpose.RATE_LIMIT)
            counted: list[tuple[str, int, int, int]] = []
            for label, key, ceiling in dimensions:
                # `redis.asyncio` ships no annotation for `eval`, so the call
                # reads as untyped here rather than anywhere in this module.
                count, ttl = await client.eval(  # type: ignore[no-untyped-call]
                    _RESERVE_SCRIPT,
                    1,
                    key,
                    self._security.step_up_attempt_window_seconds,
                    self._security.step_up_lockout_seconds,
                    ceiling,
                )
                counted.append((label, int(count), int(ttl), ceiling))
        except RedisError as exc:
            # Fails closed — see `StepUpUnavailableError`.
            logger.error("step_up_throttle_backend_unavailable", error=str(exc))
            raise StepUpUnavailableError(
                "That request could not be completed just now. Please try again."
            ) from exc

        for label, count, ttl, ceiling in counted:
            if count > ceiling:
                logger.warning("step_up_throttled", dimension=label)
                raise RateLimitExceededError(
                    "Too many attempts. Please try again later.",
                    retry_after_seconds=(
                        ttl if ttl > 0 else self._security.step_up_lockout_seconds
                    ),
                )

    async def clear_user_attempts(self, *, user_id: uuid.UUID, tenant_id: uuid.UUID | None) -> None:
        """Reset **only this account's** counter, after a correct password.

        Named for the one dimension it touches, and deliberately given no way to
        reach the other. The previous `clear()` deleted both, which meant a
        successful step-up by any account behind an address wiped the failed
        attempts of every other account behind it: an attacker holding one
        ordinary account could reset the shared budget at will simply by
        succeeding on their own.

        **The address counter is never cleared by anybody.** It drains only
        through its own bounded TTL. That is the secure behaviour, and it has a
        cost worth stating plainly: a successful step-up still counts against
        the address budget, so a shared office or CGNAT address consumes it
        through ordinary use as well as through abuse. `step_up_max_attempts_per_ip`
        is set well above the per-user ceiling for exactly that reason.

        Someone who mistypes once and then gets it right starts fresh on their
        own counter rather than carrying that failure toward a lockout. A clear
        that cannot reach Redis is logged and ignored: the counter expires on
        its own, and failing the operation *after* the password was accepted
        would punish the person who got it right.
        """
        if not self._security.rate_limit_enabled:
            return

        try:
            client = get_redis(RedisPurpose.RATE_LIMIT)
            await client.delete(self.user_key(user_id=user_id, tenant_id=tenant_id))
        except RedisError as exc:
            logger.error("step_up_throttle_clear_failed", error=str(exc))


__all__ = ["LoginThrottle", "StepUpThrottle", "StepUpUnavailableError"]
