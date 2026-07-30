"""User schemas.

``password_hash`` appears in no schema in this module. That is the practical
value of not returning ORM models directly: the field cannot leak into a
response because no response type has a slot for it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.models.user import UserRole
from app.schemas.base import CamelCaseModel, IdentifiedSchema


class UserRead(IdentifiedSchema):
    """A user as returned by the API."""

    tenant_id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: UserRole
    is_active: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None


class UserSummary(CamelCaseModel):
    """Compact user representation for embedding in other resources.

    Exists so that a list of, say, orders can name the user who created each one
    without inflating the payload with the full user record.
    """

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: UserRole


class UserCreate(CamelCaseModel):
    """Payload for creating a user.

    Defined now because the shape is settled and the users router needs a
    documented request contract. The endpoint that consumes it belongs to the
    auth phase — creating a user requires password hashing and an invitation
    flow, neither of which exists yet.

    ``tenant_id`` is deliberately absent: it is taken from the authenticated
    context, never from the request body. Accepting it here would let a caller
    create a user inside somebody else's tenant.
    """

    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole = UserRole.MEMBER


class UserUpdate(CamelCaseModel):
    """Payload for updating a user.

    Every field is optional — this is a partial update. ``email`` is excluded:
    changing an address requires re-verification, so it gets a dedicated
    endpoint rather than riding along in a generic PATCH.
    """

    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None


__all__ = ["UserCreate", "UserRead", "UserSummary", "UserUpdate"]
