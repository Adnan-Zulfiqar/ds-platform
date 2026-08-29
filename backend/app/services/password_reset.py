"""Password reset by one-time code.

**Six digits is a million possibilities.** That is small. It is workable only
because everything around it is tight, and each control below closes a specific
attack rather than being defence-in-depth decoration:

* **Keyed HMAC at rest, never a bare hash.** A million SHA-256 digests is a
  rainbow table somebody builds in seconds. `SECURITY_OTP_HMAC_KEY` is a
  dedicated secret — separate from the JWT signing key, so one leak does not
  compromise both — and the digest is domain-separated so an OTP digest can
  never be confused with a reset-ticket digest.
* **Ten-minute expiry, five attempts, one active challenge per account.** Five
  guesses out of a million, inside ten minutes, on one challenge.
* **Per-address and per-IP hourly limits**, so an attacker cannot simply request
  a fresh challenge each time they exhaust the attempts on the last one.
* **Generic responses.** Registered, unregistered, local-password and
  Google-only accounts all get the same reply. Anything else turns this endpoint
  into a list of who has an account here.
* **Atomic consume.** Verification and reset each use `MULTI`/`GET`/`DEL`/`EXEC`
  so exactly one of two concurrent callers gets the value. `GETDEL` would be the
  obvious call and is unavailable on the deployed Redis 3.0.504 — the same
  limitation that produced a production defect in EBAY-C0.1.

Nothing here logs an address, a code, a challenge id or a ticket.

**Google-only accounts.** A user who signed up with Google has no password, and
this flow will happily give them one — deliberately. Losing access to a Google
account should not mean losing the workspace, and the code is delivered to the
address Google verified, so it proves the same ownership. The alternative,
refusing, would be both a worse outcome and an account-existence oracle. This is
recorded in `docs/governance/PROCESSING_REGISTER.md`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.password import hash_password, validate_password_strength
from app.core.redis import RedisPurpose, get_redis
from app.models.user import User

logger = get_logger(__name__)

__all__ = [
    "PasswordResetService",
    "ResetChallenge",
    "ResetOutcome",
]

#: Domain separation. An OTP digest and a ticket digest are both HMACs under the
#: same key; without distinct domains a value valid as one could be presented as
#: the other.
_OTP_DOMAIN: Final[bytes] = b"droppilot/password-reset/otp/v1"
_TICKET_DOMAIN: Final[bytes] = b"droppilot/password-reset/ticket/v1"

_CHALLENGE_PREFIX: Final[str] = "pwreset:challenge:"
_ACCOUNT_PREFIX: Final[str] = "pwreset:account:"
_COOLDOWN_PREFIX: Final[str] = "pwreset:cooldown:"
_TICKET_PREFIX: Final[str] = "pwreset:ticket:"
_RATE_EMAIL_PREFIX: Final[str] = "pwreset:rl:email:"
_RATE_IP_PREFIX: Final[str] = "pwreset:rl:ip:"


def _digest(domain: bytes, value: str) -> str:
    """Keyed, domain-separated digest of a low-entropy secret."""
    key = settings.security.otp_hmac_key.get_secret_value().encode("utf-8")
    return hmac.new(key, domain + b"\x00" + value.encode("utf-8"), hashlib.sha256).hexdigest()


def _opaque(value: str) -> str:
    """Unkeyed digest, for use as a Redis key component only.

    Addresses and IPs are hashed before they become key names: a key space full
    of customer email addresses turns up in `MONITOR` output and support dumps.
    """
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ResetChallenge:
    """What the caller is told after requesting a code.

    Carries no hint about whether an account exists — the challenge id is minted
    either way, and a request for an unknown address gets one that will never
    verify.
    """

    challenge_id: str
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class ResetOutcome:
    """The single-use authority to set a new password."""

    reset_ticket: str
    expires_in_seconds: int


class PasswordResetService:
    """Request, verify and complete a password reset."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._config = settings.password_reset
        self._redis = get_redis(RedisPurpose.SESSION)

    # ---------------------------------------------------------------- helpers

    async def _consume(self, key: str) -> str | None:
        """Read and delete in one round trip, so exactly one caller wins.

        `GETDEL` is the natural call and is Redis 6.2. The deployed instance is
        3.0.504, where a transaction is the compatible equivalent — and the
        equivalent that already caused one production defect when it was
        overlooked.
        """
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.get(key)
        pipeline.delete(key)
        value, _ = await pipeline.execute()
        return str(value) if value is not None else None

    async def _within_rate_limits(self, *, email: str, client_ip: str | None) -> bool:
        """Hourly ceilings, so exhausting attempts cannot simply be repeated."""
        checks: list[tuple[str, int]] = [
            (f"{_RATE_EMAIL_PREFIX}{_opaque(email)}", self._config.requests_per_email_per_hour)
        ]
        if client_ip:
            checks.append(
                (f"{_RATE_IP_PREFIX}{_opaque(client_ip)}", self._config.requests_per_ip_per_hour)
            )

        for key, limit in checks:
            count = int(await self._redis.incr(key))
            if count == 1:
                await self._redis.expire(key, 3600)
            if count > limit:
                logger.warning("password_reset_rate_limited", dimension=key.split(":")[2])
                return False
        return True

    async def _find_user(self, email: str) -> User | None:
        return (
            await self._session.execute(
                select(User).where(
                    func.lower(User.email) == email.strip().lower(),
                    User.deleted_at.is_(None),
                    User.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

    # ---------------------------------------------------------------- request

    async def request(
        self, *, email: str, client_ip: str | None = None
    ) -> tuple[ResetChallenge, str | None]:
        """Start a challenge.

        Returns the challenge and, **only when the address belongs to a real
        account**, the plaintext code for the caller to email. The caller cannot
        tell the two cases apart from the challenge alone, which is the point.
        """
        challenge_id = secrets.token_urlsafe(32)
        challenge = ResetChallenge(
            challenge_id=challenge_id, expires_in_seconds=self._config.otp_ttl_seconds
        )

        if not await self._within_rate_limits(email=email, client_ip=client_ip):
            # Same shape as success. A distinct error here would confirm that
            # somebody has been probing this address.
            return challenge, None

        user = await self._find_user(email)
        if user is None:
            logger.info("password_reset_requested_unknown_address")
            return challenge, None

        cooldown_key = f"{_COOLDOWN_PREFIX}{user.id}"
        if await self._redis.get(cooldown_key) is not None:
            logger.info("password_reset_cooldown_active", user_id=str(user.id))
            return challenge, None

        # One active challenge per account: minting a new one retires the old,
        # so an attacker cannot accumulate parallel guessing surfaces.
        account_key = f"{_ACCOUNT_PREFIX}{user.id}"
        previous = await self._redis.get(account_key)
        if previous:
            await self._redis.delete(f"{_CHALLENGE_PREFIX}{previous}")

        code = f"{secrets.randbelow(1_000_000):06d}"
        record = {
            "user_id": str(user.id),
            "otp": _digest(_OTP_DOMAIN, code),
            "attempts": 0,
        }
        await self._redis.setex(
            f"{_CHALLENGE_PREFIX}{challenge_id}",
            self._config.otp_ttl_seconds,
            json.dumps(record),
        )
        await self._redis.setex(account_key, self._config.otp_ttl_seconds, challenge_id)
        await self._redis.setex(cooldown_key, self._config.resend_cooldown_seconds, "1")

        logger.info("password_reset_challenge_issued", user_id=str(user.id))
        return challenge, code

    async def discard(self, challenge_id: str) -> None:
        """Drop a challenge whose email never went out.

        A challenge the user cannot possibly satisfy is worse than none: they
        would sit waiting for a code that was never sent.
        """
        record = await self._consume(f"{_CHALLENGE_PREFIX}{challenge_id}")
        if record:
            try:
                user_id = json.loads(record)["user_id"]
            except (ValueError, KeyError):
                return
            await self._redis.delete(f"{_ACCOUNT_PREFIX}{user_id}", f"{_COOLDOWN_PREFIX}{user_id}")

    # ----------------------------------------------------------------- verify

    async def verify(self, *, challenge_id: str, code: str) -> ResetOutcome | None:
        """Exchange a correct code for a single-use ticket. `None` on failure."""
        key = f"{_CHALLENGE_PREFIX}{challenge_id}"
        raw = await self._redis.get(key)
        if raw is None:
            return None

        try:
            record: dict[str, Any] = json.loads(raw)
        except ValueError:
            await self._redis.delete(key)
            return None

        attempts = int(record.get("attempts", 0)) + 1
        if attempts > self._config.max_verification_attempts:
            # Burn the challenge rather than let it be ground down.
            await self._redis.delete(key)
            logger.warning("password_reset_attempts_exhausted")
            return None

        if not hmac.compare_digest(str(record.get("otp", "")), _digest(_OTP_DOMAIN, code)):
            record["attempts"] = attempts
            ttl = await self._redis.ttl(key)
            await self._redis.setex(key, max(int(ttl), 1), json.dumps(record))
            return None

        # Correct. Consume atomically so two concurrent verifications of the
        # same code cannot both mint a ticket.
        if await self._consume(key) is None:
            return None

        user_id = str(record["user_id"])
        await self._redis.delete(f"{_ACCOUNT_PREFIX}{user_id}")

        ticket = secrets.token_urlsafe(32)
        await self._redis.setex(
            f"{_TICKET_PREFIX}{_digest(_TICKET_DOMAIN, ticket)}",
            self._config.ticket_ttl_seconds,
            user_id,
        )
        logger.info("password_reset_verified", user_id=user_id)
        return ResetOutcome(reset_ticket=ticket, expires_in_seconds=self._config.ticket_ttl_seconds)

    # --------------------------------------------------------------- complete

    async def complete(self, *, reset_ticket: str, new_password: str) -> uuid.UUID | None:
        """Set the new password. Returns the user id, or `None` if the ticket is spent.

        The ticket is consumed **before** the password is validated, so a weak
        password does not hand back a second attempt at a single-use authority.
        The caller is responsible for revoking sessions.
        """
        user_id_raw = await self._consume(
            f"{_TICKET_PREFIX}{_digest(_TICKET_DOMAIN, reset_ticket)}"
        )
        if user_id_raw is None:
            return None

        user_id = uuid.UUID(user_id_raw)
        user = (
            await self._session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            return None

        # The existing policy and the existing Argon2id authority. No second
        # implementation of either.
        validate_password_strength(new_password, email=user.email)
        user.password_hash = hash_password(new_password)
        await self._session.flush()

        logger.info("password_reset_completed", user_id=str(user_id))
        return user_id
