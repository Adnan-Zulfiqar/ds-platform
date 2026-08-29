"""One-time, intent-bound nonces for Google sign-in.

Independent review found the previous version had two holes, both of which this
module exists to close.

**The nonce was optional.** The request schema allowed it to be absent, and the
endpoint only checked it when present — so a caller could simply omit it and
skip replay protection entirely. A defence a client can decline is not a
defence. Issuing is now the only way to obtain one, and every credential
endpoint requires it.

**The nonce carried no intent.** One value worked for signing in, signing up or
linking, so a nonce obtained on the public login page could be presented to the
link endpoint. Each nonce is now bound server-side to exactly one operation, and
a link nonce additionally records *which user and tenant* asked for it, so a
credential cannot be redirected onto another account.

Consumption is a `MULTI`/`GET`/`DEL` transaction, so of two concurrent uses
exactly one sees the value. `GETDEL` would be the natural call and is Redis 6.2;
the deployed instance is 3.0.504.
"""

from __future__ import annotations

import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis

logger = get_logger(__name__)

__all__ = ["GoogleIntent", "GoogleNonceStore", "NonceRecord"]

_PREFIX: Final[str] = "google:nonce:"


class GoogleIntent(StrEnum):
    """What a nonce may be used for. Never inferred from the request."""

    LOGIN = "login"
    SIGNUP = "signup"
    LINK = "link"


@dataclass(frozen=True, slots=True)
class NonceRecord:
    """A consumed nonce, and what the server bound to it when it was issued."""

    intent: GoogleIntent
    user_id: uuid.UUID | None
    tenant_id: uuid.UUID | None

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        return f"<NonceRecord intent={self.intent}>"


class GoogleNonceStore:
    """Issue and consume single-use, intent-bound nonces."""

    def __init__(self) -> None:
        self._redis = get_redis(RedisPurpose.SESSION)
        self._ttl = settings.google_oauth.nonce_ttl_seconds

    async def issue(
        self,
        intent: GoogleIntent,
        *,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> str:
        """Mint a nonce bound to one operation.

        `user_id`/`tenant_id` are recorded for `LINK` so the credential can only
        be attached to the account that asked. They come from the authenticated
        session at issue time, never from the later request body.
        """
        if intent is GoogleIntent.LINK and (user_id is None or tenant_id is None):
            raise ValueError("a link nonce must be bound to a user and tenant")

        nonce = secrets.token_urlsafe(32)
        record = {
            "intent": intent.value,
            "user_id": str(user_id) if user_id else None,
            "tenant_id": str(tenant_id) if tenant_id else None,
        }
        await self._redis.setex(f"{_PREFIX}{nonce}", self._ttl, json.dumps(record))
        return nonce

    async def consume(self, nonce: str, *, expected: GoogleIntent) -> NonceRecord | None:
        """Spend a nonce, or refuse.

        Returns `None` for a nonce that is missing, blank, unknown, expired,
        already spent, or issued for a different operation. The caller turns
        every one of those into the same generic failure: distinguishing them
        tells an attacker which to try next.
        """
        if not nonce or not nonce.strip():
            return None

        # Atomic: of two concurrent callers, exactly one sees the value.
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.get(f"{_PREFIX}{nonce}")
        pipeline.delete(f"{_PREFIX}{nonce}")
        raw, _ = await pipeline.execute()
        if raw is None:
            return None

        try:
            stored = json.loads(raw)
            intent = GoogleIntent(stored["intent"])
        except (ValueError, KeyError, TypeError):
            logger.warning("google_nonce_malformed")
            return None

        if intent is not expected:
            # A login nonce presented to the link endpoint, or the reverse.
            logger.warning("google_nonce_wrong_intent", expected=expected.value)
            return None

        return NonceRecord(
            intent=intent,
            user_id=uuid.UUID(stored["user_id"]) if stored.get("user_id") else None,
            tenant_id=uuid.UUID(stored["tenant_id"]) if stored.get("tenant_id") else None,
        )

    @staticmethod
    def matches(token_nonce: str | None, issued_nonce: str) -> bool:
        """Compare the nonce inside the credential with the one we issued.

        `compare_digest` rather than `==`: the values are secrets, and a
        short-circuiting comparison leaks their prefix through timing.
        """
        if not token_nonce:
            return False
        return hmac.compare_digest(token_nonce, issued_nonce)
