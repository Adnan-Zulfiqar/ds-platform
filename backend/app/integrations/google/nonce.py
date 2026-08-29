"""One-time nonces for Google sign-in attempts.

Google embeds the nonce in the credential it signs, so checking it here proves
the token was minted for *this* sign-in attempt rather than captured from
another one and replayed.

Stored server-side and consumed on use. A nonce that were merely unpredictable
but not recorded would prove the browser generated a fresh value, not that the
server ever asked for one — and a replayed credential carries a perfectly
well-formed nonce.

Consumption uses `MULTI`/`GET`/`DEL` rather than `GETDEL`: the deployed Redis is
3.0.504, and `GETDEL` is 6.2. That exact gap produced a production defect in
EBAY-C0.1, so it is spelled out here rather than rediscovered.
"""

from __future__ import annotations

import secrets
from typing import Final

from app.core.config import settings
from app.core.redis import RedisPurpose, get_redis

__all__ = ["GoogleNonceStore"]

_PREFIX: Final[str] = "google:nonce:"


class GoogleNonceStore:
    """Issue and consume single-use sign-in nonces."""

    def __init__(self) -> None:
        self._redis = get_redis(RedisPurpose.SESSION)
        self._ttl = settings.google_oauth.nonce_ttl_seconds

    async def issue(self) -> str:
        nonce = secrets.token_urlsafe(24)
        await self._redis.setex(f"{_PREFIX}{nonce}", self._ttl, "1")
        return nonce

    async def consume(self, nonce: str) -> bool:
        """Whether this nonce was outstanding. Never true twice."""
        if not nonce:
            return False
        pipeline = self._redis.pipeline(transaction=True)
        pipeline.get(f"{_PREFIX}{nonce}")
        pipeline.delete(f"{_PREFIX}{nonce}")
        value, _ = await pipeline.execute()
        return value is not None
