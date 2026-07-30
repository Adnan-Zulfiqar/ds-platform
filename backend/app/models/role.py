"""Role and user-role assignment.

**Roles are platform-global, not per-tenant.** ``Role`` inherits
``ReferenceBase``, so it carries no ``tenant_id``. Every tenant draws from the
same four roles, which keeps authorization checks a simple name comparison and
avoids seeding four rows per tenant on signup.

The trade-off is that a tenant cannot define a custom role. That is the right
call now — custom roles need a permission model to attach to, and inventing one
before any feature needs permissions would be guesswork. When it arrives, a
nullable ``tenant_id`` on this table makes global roles and tenant-defined roles
coexist without migrating existing rows.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ReferenceBase


class RoleName(StrEnum):
    """Canonical role slugs.

    Renamed from Phase 0's ``UserRole`` enum, which had to give up the name so
    that the ``UserRole`` association table specified for Phase 1 could take it.
    Two different concepts sharing one identifier in the same package is a
    guaranteed source of import mistakes.

    These are the seeded values of ``roles.name``. The enum exists so that
    application code compares against a checked constant rather than a string
    literal that a typo would silently turn into "no match, deny".
    """

    OWNER = "owner"  # billing control; cannot be removed from a tenant
    ADMIN = "admin"  # full operational access
    MEMBER = "member"  # day-to-day operations
    VIEWER = "viewer"  # read-only

    @property
    def rank(self) -> int:
        """Position in the privilege hierarchy; higher outranks lower.

        Lets a check express "admin or above" without enumerating every role
        above admin — which would silently miss any role added later.
        """
        return _ROLE_RANK[self]


_ROLE_RANK: dict[RoleName, int] = {
    RoleName.VIEWER: 0,
    RoleName.MEMBER: 10,
    RoleName.ADMIN: 20,
    RoleName.OWNER: 30,
}


class Role(ReferenceBase):
    """A named role available to every tenant."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<Role id={self.id} name={self.name!r}>"


class UserRole(Base):
    """Assignment of a role to a user.

    A plain association table with a composite primary key rather than a
    surrogate id: the pair *is* the identity, and the composite key enforces
    "a user holds a given role at most once" without a separate unique
    constraint.

    No timestamps or soft delete. Removing a role assignment should remove the
    row — a soft-deleted assignment that a query forgot to filter would grant
    privileges that were explicitly revoked, which is the worst possible
    direction for this table to fail in.

    Both foreign keys cascade: deleting a user or a role removes the assignment
    rather than leaving a row pointing at nothing.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (
        # The composite primary key already indexes (user_id, role_id) in that
        # order, which serves "roles for this user" — the query run on every
        # login. This index serves the reverse, "users holding this role",
        # needed by admin screens and by any future permission audit.
        Index("ix_user_roles_role_id_user_id", "role_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<UserRole user_id={self.user_id} role_id={self.role_id}>"


__all__ = ["Role", "RoleName", "UserRole"]
