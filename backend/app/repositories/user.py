"""User repository.

Users are tenant-owned, so this uses the scoped base and every query is confined
to the current tenant automatically.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.base import TenantScopedRepository


class UserRepository(TenantScopedRepository[User]):
    sortable_fields = frozenset(
        {"created_at", "updated_at", "email", "full_name", "role", "last_login_at"}
    )
    searchable_fields = frozenset({"email", "full_name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_by_email(self, email: str) -> User | None:
        """Look up a user by email within the current tenant.

        Email is normalised to lowercase because addresses are compared
        case-insensitively in practice, and storing mixed case would let
        ``Alice@x.com`` and ``alice@x.com`` become two accounts that both look
        correct to the customer.
        """
        query = self._base_query().where(User.email == email.strip().lower())
        return (await self.session.execute(query)).scalar_one_or_none()

    async def email_taken(self, email: str) -> bool:
        return await self.get_by_email(email) is not None


__all__ = ["UserRepository"]
