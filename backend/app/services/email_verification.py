"""Issue and consume email verification tokens."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.email_verification import EmailVerificationToken
from app.models.user import User
from app.repositories.base import TenantScopedRepository
from app.services.base import BaseService
from app.services.mailer import EmailMessage, get_mailer


class EmailVerificationTokenRepository(TenantScopedRepository[EmailVerificationToken]):
    sortable_fields = frozenset({"created_at", "expires_at"})
    searchable_fields = frozenset()

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EmailVerificationToken)

    async def get_by_hash(self, token_hash: str) -> EmailVerificationToken | None:
        query = self._base_query().where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.consumed_at.is_(None),
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class EmailVerificationService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.tokens = EmailVerificationTokenRepository(session)
        self.mailer = get_mailer()

    async def request_for_user(self, user: User) -> str:
        """Create a token and hand it to the mailer.

        Returns the raw token so tests can confirm without a mailbox. Production
        callers must not expose this value in API responses.
        """
        if user.is_verified:
            raise ValidationError("This account is already verified.")

        raw = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(
            hours=settings.security.email_verification_ttl_hours
        )
        await self.tokens.create(
            user_id=user.id,
            token_hash=_hash_token(raw),
            expires_at=expires,
        )
        await self.session.flush()
        await self.mailer.send(
            EmailMessage(
                to=user.email,
                subject="Verify your DropPilot account",
                body_text=(
                    "Use this verification token in the app to confirm your email:\n\n"
                    f"{raw}\n\n"
                    "If you did not create an account, ignore this message."
                ),
            )
        )
        return raw

    async def confirm(self, *, raw_token: str, user: User) -> User:
        """Consume a token for the authenticated user.

        Confirmation is authenticated on purpose: an unscoped lookup by token
        hash would be a third unscoped repository, and the user is already
        signed in after registration — they only lack the verified flag.
        """
        token_hash = _hash_token(raw_token.strip())
        record = await self.tokens.get_by_hash(token_hash)
        if record is None or record.user_id != user.id:
            raise NotFoundError("Verification token is invalid or has already been used.")
        if record.expires_at < datetime.now(UTC):
            raise ValidationError("Verification token has expired.")

        user.is_verified = True
        record.consumed_at = datetime.now(UTC)
        await self.session.flush()
        return user
