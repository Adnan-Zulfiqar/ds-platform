"""Team invitations (Track E4). Tenant-scoped like every business table.

Acceptance arrives without a session, so the service binds the tenant named
in the link before using this repository; see ``TeamInvitationService`` for
why that is safe. Nothing here searches across tenants.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invitation import UserInvitation
from app.repositories.base import TenantScopedRepository


class UserInvitationRepository(TenantScopedRepository[UserInvitation]):
    sortable_fields = frozenset({"created_at", "expires_at", "email"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserInvitation)

    def _open(self) -> Select[tuple[UserInvitation]]:
        return self._base_query().where(
            UserInvitation.accepted_at.is_(None), UserInvitation.revoked_at.is_(None)
        )

    async def open_for_email(self, email: str) -> UserInvitation | None:
        result = await self.session.execute(self._open().where(UserInvitation.email == email))
        return result.scalar_one_or_none()

    async def list_open(self, *, limit: int) -> list[UserInvitation]:
        """Open invitations, newest first, including expired ones (so an admin
        sees that a link lapsed and can send a fresh one)."""
        result = await self.session.execute(
            self._open().order_by(UserInvitation.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def count_open_unexpired(self) -> int:
        query = select(func.count()).select_from(
            self._open().where(UserInvitation.expires_at > datetime.now(UTC)).subquery()
        )
        return int((await self.session.execute(query)).scalar_one())

    async def open_by_token_hash(self, token_hash: str, *, lock: bool) -> UserInvitation | None:
        """``lock`` for acceptance, so two acceptances of one link cannot both
        create a user."""
        query = self._open().where(UserInvitation.token_hash == token_hash)
        if lock:
            query = query.with_for_update()
        return (await self.session.execute(query)).scalar_one_or_none()


__all__ = ["UserInvitationRepository"]
