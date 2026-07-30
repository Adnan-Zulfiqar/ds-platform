"""User repositories.

Two classes with deliberately different scoping:

* :class:`UserRepository` — tenant-scoped. Everything an authenticated request
  does goes through this.
* :class:`AuthenticationUserRepository` — **unscoped**, and usable only on the
  login path, where no tenant is known yet. See its docstring.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.base import BaseRepository, TenantScopedRepository


def normalise_email(email: str) -> str:
    """Lowercase and trim an address for storage and comparison.

    Addresses are compared case-insensitively in practice, so storing mixed case
    would let ``Alice@x.com`` and ``alice@x.com`` become two accounts that both
    look correct to the customer and neither of which reliably receives mail.

    Only the whole address is lowercased — the local part is technically
    case-sensitive per RFC 5321, but no mail provider in practice treats it that
    way, and honouring the letter of the spec would create the duplicate-account
    problem above.
    """
    return email.strip().lower()


class UserRepository(TenantScopedRepository[User]):
    """Tenant-scoped user access."""

    sortable_fields = frozenset(
        {"created_at", "updated_at", "email", "first_name", "last_name", "last_login_at"}
    )
    searchable_fields = frozenset({"email", "first_name", "last_name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get_by_email(self, email: str) -> User | None:
        """Look up a user by email within the current tenant."""
        query = self._base_query().where(User.email == normalise_email(email))
        return (await self.session.execute(query)).scalar_one_or_none()

    async def email_taken(self, email: str) -> bool:
        return await self.get_by_email(email) is not None


class AuthenticationUserRepository(BaseRepository[User]):
    """Unscoped user lookup, for the authentication path only.

    **Why this exists.** Login receives an email address and a password and
    nothing else. The tenant is a *result* of authentication, not an input to
    it, so the lookup that finds the user cannot be filtered by a tenant that is
    not yet known. ``UserRepository`` would raise, correctly, because no tenant
    context is bound.

    **Why it is a separate class rather than a method on the scoped one.**
    A method that quietly skipped the tenant filter would be one autocomplete
    away from being used on an ordinary request path, and the resulting bug —
    an endpoint returning every tenant's users — would look like working code.
    A separate, explicitly-named class cannot be reached by accident.

    **The rule:** the only caller is ``AuthService``, and only before a
    principal has been established. Everything afterwards uses
    ``UserRepository``.
    """

    sortable_fields = frozenset({"created_at", "email"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def find_by_email(self, email: str) -> Sequence[User]:
        """Return every live user holding this address, across all tenants.

        Returns a sequence, not a single row, because ``users`` is unique on
        ``(tenant_id, email)`` rather than on ``email`` alone — the same person
        may legitimately hold accounts in two different tenants. Collapsing that
        to ``scalar_one_or_none()`` would raise ``MultipleResultsFound`` in
        production the first time a consultant joined a second customer.

        Ordered by creation so that the caller's tie-breaking is deterministic
        rather than dependent on physical row order.
        """
        query = (
            select(User)
            .where(User.email == normalise_email(email), User.deleted_at.is_(None))
            .order_by(User.created_at.asc())
        )
        return (await self.session.execute(query)).scalars().all()

    async def get_by_id_unscoped(self, user_id: uuid.UUID) -> User | None:
        """Fetch a user by id without a tenant filter.

        Used when exchanging a refresh token, where the token record identifies
        the user before any tenant context exists. The caller must bind context
        from the resulting user before doing anything else.
        """
        query = select(User).where(User.id == user_id, User.deleted_at.is_(None))
        return (await self.session.execute(query)).scalar_one_or_none()


__all__ = ["AuthenticationUserRepository", "UserRepository", "normalise_email"]
