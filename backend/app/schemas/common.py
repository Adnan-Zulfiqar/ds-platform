"""Shared request and response schemas: pagination, sorting, filtering, errors.

Every list endpoint in the platform accepts the same query parameters and
returns the same envelope shape. Defining that once here means a client that can
paginate products can paginate orders without relearning anything, and it keeps
the OpenAPI document consistent.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from math import ceil
from typing import Annotated, Generic, Self, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import settings
from app.schemas.base import AppBaseModel, CamelCaseModel

T = TypeVar("T")


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class PaginationParams(BaseModel):
    """Page-based pagination parameters.

    **Why offset pagination.** It supports arbitrary page jumps and total counts,
    which the UI needs for a page-number control. The cost is that deep offsets
    degrade — Postgres must walk and discard every skipped row, so page 10,000
    of a million-row table is slow.

    That is acceptable now because tenant-scoped result sets are far smaller than
    the platform-wide total, and no UI in Phase 0 paginates deeply. When product
    catalogues reach the millions per tenant, add a keyset (cursor) variant
    alongside this rather than replacing it — cursors cannot express "jump to
    page 400", so both have a place.

    ``max_page_size`` is enforced by configuration so a client cannot request a
    million rows in one call and exhaust the API process's memory.
    """

    page: Annotated[int, Field(ge=1, description="1-indexed page number.")] = 1
    size: Annotated[int, Field(ge=1, description="Items per page.")] = Field(
        default_factory=lambda: settings.default_page_size
    )

    @model_validator(mode="after")
    def _clamp_size(self) -> Self:
        """Clamp rather than reject an oversized page.

        Returning fewer rows than asked is a better outcome for a client that
        guessed high than a 422 they must special-case.
        """
        if self.size > settings.max_page_size:
            object.__setattr__(self, "size", settings.max_page_size)
        return self

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size

    @property
    def limit(self) -> int:
        return self.size


class SortParams(BaseModel):
    """Sorting parameters.

    ``sort_by`` names a field; the repository validates it against a per-model
    allowlist. Interpolating a client-supplied column name into ORDER BY without
    that check is a SQL injection vector.
    """

    sort_by: str | None = Field(default=None, description="Field name to sort by.")
    sort_dir: SortDirection = SortDirection.DESC


class SearchParams(BaseModel):
    """Free-text search parameters."""

    q: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Free-text search term.",
    )


class ListQueryParams(PaginationParams, SortParams, SearchParams):
    """Combined query parameters for list endpoints.

    Endpoints depend on this via ``Depends`` so FastAPI expands it into
    individual documented query parameters rather than expecting a JSON body.
    """


def list_query_params(
    page: Annotated[int, Query(ge=1, description="1-indexed page number.")] = 1,
    size: Annotated[int, Query(ge=1, le=500, description="Items per page.")] | None = None,
    sort_by: Annotated[str | None, Query(description="Field to sort by.")] = None,
    sort_dir: Annotated[SortDirection, Query(description="Sort direction.")] = SortDirection.DESC,
    q: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
) -> ListQueryParams:
    """FastAPI dependency producing validated list query parameters."""
    return ListQueryParams(
        page=page,
        size=size if size is not None else settings.default_page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
        q=q,
    )


class PageMeta(CamelCaseModel):
    """Pagination metadata accompanying a page of results."""

    page: int
    size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool

    @classmethod
    def build(cls, *, page: int, size: int, total_items: int) -> PageMeta:
        total_pages = ceil(total_items / size) if size else 0
        return cls(
            page=page,
            size=size,
            total_items=total_items,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        )


class Page(CamelCaseModel, Generic[T]):
    """A page of results.

    Generic so that ``Page[ProductRead]`` produces a correctly typed OpenAPI
    schema and a correctly typed client. Wrapping results in an object rather
    than returning a bare array leaves room to add fields — an aggregate count,
    a cursor — without a breaking change.
    """

    items: Sequence[T]
    meta: PageMeta

    @classmethod
    def build(
        cls,
        *,
        items: Sequence[T],
        page: int,
        size: int,
        total_items: int,
    ) -> Page[T]:
        return cls(items=items, meta=PageMeta.build(page=page, size=size, total_items=total_items))


class ErrorDetail(CamelCaseModel):
    """A single field-level error."""

    field: str | None = Field(default=None, description="Dotted path to the offending field.")
    message: str
    type: str | None = Field(default=None, description="Machine-readable error type.")


class ErrorResponse(CamelCaseModel):
    """The response body returned for every error.

    One shape for all failures — validation, auth, not-found, unhandled — so a
    client needs exactly one error-handling path.

    ``code`` is the stable contract; ``message`` is for humans and may change.
    ``request_id`` lets a customer quote an identifier that maps directly to the
    server-side log line, which turns most support tickets into a single query.
    """

    code: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Human-readable description. Do not branch on this.")
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = None

    model_config = ConfigDict(
        **CamelCaseModel.model_config,
        json_schema_extra={
            "example": {
                "code": "validation_error",
                "message": "The request payload failed validation.",
                "details": [
                    {
                        "field": "email",
                        "message": "value is not a valid email address",
                        "type": "value_error",
                    }
                ],
                "requestId": "01J8XW2M4T7K9Q0R",
            }
        },
    )


class MessageResponse(CamelCaseModel):
    """Trivial acknowledgement body for endpoints with nothing to return."""

    message: str


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(CamelCaseModel):
    """Health of one dependency."""

    name: str
    status: HealthStatus
    detail: str | None = None


class HealthResponse(CamelCaseModel):
    """Aggregate health report."""

    status: HealthStatus
    version: str
    environment: str
    components: list[ComponentHealth] = Field(default_factory=list)


__all__ = [
    "AppBaseModel",
    "ComponentHealth",
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "HealthStatus",
    "ListQueryParams",
    "MessageResponse",
    "Page",
    "PageMeta",
    "PaginationParams",
    "SearchParams",
    "SortDirection",
    "SortParams",
    "list_query_params",
]
