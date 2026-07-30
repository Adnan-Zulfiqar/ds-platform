"""User schemas.

``password_hash`` appears in no schema in this module. That is the practical
value of not returning ORM models directly: the field cannot leak into a
response because no response type has a slot for it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.base import CamelCaseModel, IdentifiedSchema


class UserRead(IdentifiedSchema):
    """A user as returned by the API."""

    tenant_id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
    full_name: str | None = Field(
        default=None,
        description="Convenience display name assembled from the name parts.",
    )
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None


class UserSummary(CamelCaseModel):
    """Compact user representation for embedding in other resources.

    Exists so that a list of, say, orders can name the user who created each one
    without inflating the payload with the full user record.
    """

    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None


class UserCreate(CamelCaseModel):
    """Payload for creating a user.

    Consumed by the team-invitation flow, which is a later phase — registration
    has its own schema in ``app.schemas.auth`` because it creates a tenant too.

    ``tenant_id`` is deliberately absent: it is taken from the authenticated
    context, never from the request body. Accepting it here would let a caller
    create a user inside somebody else's tenant.

    ``password`` is absent for the same class of reason: an invited user sets
    their own password through a signed link, so no administrator ever chooses
    or transmits it.
    """

    email: EmailStr
    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)


class UserUpdate(CamelCaseModel):
    """Payload for updating a user.

    Every field is optional — this is a partial update. ``email`` is excluded:
    changing an address requires re-verification, so it gets a dedicated
    endpoint rather than riding along in a generic PATCH.
    """

    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None


__all__ = ["UserCreate", "UserRead", "UserSummary", "UserUpdate"]
