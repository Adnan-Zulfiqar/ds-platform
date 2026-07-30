"""Role and role-assignment repository.

Extends :class:`BaseRepository`, not the tenant-scoped variant: ``roles`` is
platform-global reference data with no ``tenant_id``. Role *assignments* are
reached through a user, and every user lookup is already tenant-scoped, so
isolation is preserved without this table carrying a discriminator.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.role import Role, RoleName, UserRole
from app.repositories.base import BaseRepository


class RoleRepository(BaseRepository[Role]):
    sortable_fields = frozenset({"created_at", "updated_at", "name"})
    searchable_fields = frozenset({"name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Role)

    async def get_by_name(self, name: RoleName | str) -> Role | None:
        value = name.value if isinstance(name, RoleName) else name
        query = self._base_query().where(Role.name == value)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_by_name_or_raise(self, name: RoleName | str) -> Role:
        """Fetch a seeded role, raising if it is absent.

        Absence means migration ``0002`` did not run or its seed data was
        deleted. That is an environment fault rather than a client error, and
        failing loudly is far better than silently registering a user with no
        role — which would leave an account nobody can use and no obvious cause.
        """
        role = await self.get_by_name(name)
        if role is None:
            value = name.value if isinstance(name, RoleName) else name
            raise NotFoundError.for_resource("Role", value)
        return role

    async def list_role_names_for_user(self, user_id: uuid.UUID) -> frozenset[str]:
        """Return the role names assigned to a user.

        Joins rather than loading ``UserRole`` rows and resolving each: one
        query instead of N+1, and it runs on every login and every token
        refresh.
        """
        query = (
            select(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        result = await self.session.execute(query)
        return frozenset(result.scalars().all())

    async def assign(self, *, user_id: uuid.UUID, role_id: uuid.UUID) -> UserRole:
        """Assign a role to a user."""
        assignment = UserRole(user_id=user_id, role_id=role_id)
        self.session.add(assignment)
        await self.session.flush()
        return assignment

    async def assign_by_name(self, *, user_id: uuid.UUID, name: RoleName) -> UserRole:
        role = await self.get_by_name_or_raise(name)
        return await self.assign(user_id=user_id, role_id=role.id)

    async def revoke(self, *, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        """Remove a role assignment.

        A hard delete, unlike everything else in this codebase. A soft-deleted
        assignment that some query forgot to filter would grant a privilege that
        was explicitly revoked — the one direction in which this table must
        never fail.
        """
        await self.session.execute(
            delete(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id)
        )
        await self.session.flush()

    async def list_all(self) -> Sequence[Role]:
        """Return every role. The set is small and fixed."""
        return (await self.session.execute(self._base_query())).scalars().all()


__all__ = ["RoleRepository"]
