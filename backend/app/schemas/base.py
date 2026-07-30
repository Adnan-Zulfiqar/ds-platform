"""Base Pydantic schema classes.

Schemas are the API contract. ORM models are never returned directly from an
endpoint — doing so exposes every column, which is how internal fields such as
``password_hash`` leak into responses. Each endpoint declares an explicit
response schema listing exactly what is public.

Request and response schemas are separate types even when their fields overlap.
Merging them forces optional-everything fields to satisfy both directions, which
throws away validation strength on input and nullability guarantees on output.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class AppBaseModel(BaseModel):
    """Base for every schema in the application."""

    model_config = ConfigDict(
        # Reject unknown fields rather than ignoring them. A client sending
        # `{"quantitiy": 5}` should get a 422, not a silently dropped typo.
        extra="forbid",
        # Strip incidental whitespace from all string input.
        str_strip_whitespace=True,
        # Validate on assignment too, so mutation after construction cannot
        # bypass the constraints that construction enforced.
        validate_assignment=True,
        frozen=False,
    )


class ORMBaseModel(AppBaseModel):
    """Base for schemas constructed from ORM instances.

    ``from_attributes`` lets ``Model.model_validate(orm_object)`` read attributes
    instead of dict keys.
    """

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class CamelCaseModel(ORMBaseModel):
    """Response base that serialises to camelCase.

    The API speaks camelCase because its primary consumer is a TypeScript
    frontend where snake_case fields are foreign. Python code keeps snake_case
    internally; the alias generator bridges the two at the boundary, so neither
    language has to compromise its conventions.

    ``populate_by_name`` keeps both spellings acceptable on input, which avoids
    breaking any internal caller that constructs a schema positionally.
    """

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class IdentifiedSchema(CamelCaseModel):
    """Mixin for entities exposing their identifier and audit timestamps.

    ``deleted_at`` is intentionally absent. Soft-deleted rows are filtered out
    before they reach a response, so exposing the column would only ever be
    misleading.
    """

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


__all__ = [
    "AppBaseModel",
    "CamelCaseModel",
    "IdentifiedSchema",
    "ORMBaseModel",
]
