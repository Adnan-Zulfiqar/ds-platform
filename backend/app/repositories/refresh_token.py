"""Refresh token repository.

Deliberately **not** tenant-scoped. The lookup that starts a refresh happens
before identity exists: a bare token arrives, and only once this repository
finds the matching row and validates it does the system know whose session it
is. A tenant filter would have to be satisfied before it could be derived.

Isolation is preserved structurally instead — every row points at a user, and
that user belongs to exactly one tenant. Callers must resolve the user through
the tenant-scoped user repository before trusting anything about the session.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.refresh_token import RefreshToken

logger = get_logger(__name__)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest used as a token's stored fingerprint.

    SHA-256 rather than Argon2 because a refresh token is 256 bits of
    cryptographic randomness with no guessable structure. Slow, memory-hard
    hashing exists to make low-entropy human passwords expensive to brute-force;
    against a random 256-bit value it buys nothing and would add real latency to
    every session renewal.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token_secret() -> str:
    """Generate the random component embedded in a refresh token."""
    return secrets.token_urlsafe(32)


class RefreshTokenRepository:
    """Data access for refresh token records.

    Standalone rather than a :class:`BaseRepository` subclass. That base is
    generic over models carrying a UUID id *and* audit timestamps, which
    ``RefreshToken`` deliberately does not — it is written once and only ever
    revoked, so an ``updated_at`` column would be dead weight. Adding the column
    purely to satisfy a type bound would be letting the abstraction dictate the
    schema.

    It also needs none of what the base provides: no pagination, no sorting, no
    soft delete, and no tenant filter (see the module docstring). The access
    pattern here is entirely token-shaped.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_token(self, token: str) -> RefreshToken | None:
        """Look up a token record by the presented token value.

        The token itself is never stored, so the lookup is by hash. Note this
        returns revoked and expired rows too — the caller must check
        ``is_usable()``. That is deliberate: distinguishing "no such token" from
        "a token that was revoked" is what makes reuse detection possible.
        """
        query = select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))
        return (await self.session.execute(query)).scalar_one_or_none()

    async def create_for_user(
        self,
        *,
        user_id: uuid.UUID,
        token: str,
        expires_at: datetime,
    ) -> RefreshToken:
        record = RefreshToken(
            user_id=user_id,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Revoke every live token for a user. Returns the number revoked.

        Used for "sign out everywhere", after a password change, and on refresh
        token reuse detection. A bulk UPDATE rather than loading each row: the
        count is unbounded and this runs during incident response, when it must
        be fast and must not depend on how many sessions exist.
        """
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
        # `rowcount` exists on the cursor result returned by UPDATE and DELETE,
        # but `execute` is typed as returning the general Result.
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    async def count_active_for_user(self, user_id: uuid.UUID) -> int:
        """Count a user's live sessions."""
        now = datetime.now(UTC)
        query = select(func.count()).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        return int((await self.session.execute(query)).scalar_one())

    async def purge_expired(self, *, before: datetime | None = None) -> int:
        """Delete expired token rows. Returns the number removed.

        A hard delete — the exception to the soft-delete rule is justified
        because an expired token has no audit value and the table would
        otherwise grow without bound, one row per login per user forever.
        Intended to run as a scheduled job once background work exists.
        """
        cutoff = before or datetime.now(UTC)
        result = await self.session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
        )
        await self.session.flush()
        removed = int(cast("CursorResult[Any]", result).rowcount or 0)
        if removed:
            logger.info("refresh_tokens_purged", count=removed)
        return removed


__all__ = ["RefreshTokenRepository", "generate_token_secret", "hash_token"]
