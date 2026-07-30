"""Tests for tenant isolation in the repository layer.

These are the most important tests in the suite. A regression here is a
cross-tenant data leak, which is the single worst failure mode a multi-tenant
SaaS platform has.

They run without a database by compiling the SQLAlchemy statement and inspecting
the generated SQL. That is a deliberate choice: it means the guarantee is checked
on every commit in CI without needing Postgres, so there is no excuse to skip it.
Integration tests that exercise the same property against real data live in
``tests/integration``.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.core.exceptions import ConflictError, ValidationError
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.common import ListQueryParams, SortDirection

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    """Render a statement as literal PostgreSQL for inspection."""
    return str(
        query.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.fixture
def repository() -> UserRepository:
    """A repository over a mock session.

    No database is involved — only statement construction is under test.
    """
    return UserRepository(MagicMock())


def test_base_query_filters_by_bound_tenant(
    repository: UserRepository, tenant_id: uuid.UUID
) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(repository._base_query())

    assert "users.tenant_id" in sql
    assert str(tenant_id) in sql


def test_base_query_excludes_soft_deleted_rows(
    repository: UserRepository, tenant_id: uuid.UUID
) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(repository._base_query())

    assert "deleted_at IS NULL" in sql


def test_base_query_raises_without_tenant_context(repository: UserRepository) -> None:
    """Missing context must fail, never fall back to an unfiltered query.

    This is the property that turns "a developer forgot to scope a query" from a
    silent data leak into a loud error.
    """
    clear_context()
    with pytest.raises(MissingTenantContextError):
        repository._base_query()


def test_different_tenants_produce_different_predicates(
    repository: UserRepository, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    set_tenant_id(tenant_id)
    first = _compile(repository._base_query())

    set_tenant_id(other_tenant_id)
    second = _compile(repository._base_query())

    assert first != second
    assert str(other_tenant_id) not in first
    assert str(tenant_id) not in second


async def test_create_rejects_a_foreign_tenant_id(
    repository: UserRepository, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """Writing into another tenant must raise, not be silently corrected.

    Silently overwriting the supplied id would hide the bug that produced it.
    """
    set_tenant_id(tenant_id)

    with pytest.raises(ConflictError):
        await repository.create(email="x@example.com", tenant_id=other_tenant_id)


def test_sorting_rejects_a_field_not_on_the_allowlist(
    repository: UserRepository, tenant_id: uuid.UUID
) -> None:
    """An arbitrary sort field must be refused.

    ``sort_by`` comes straight from the query string; resolving it to a column
    without an allowlist is an injection vector.
    """
    set_tenant_id(tenant_id)
    params = ListQueryParams(sort_by="password_hash", sort_dir=SortDirection.ASC)

    with pytest.raises(ValidationError):
        repository._apply_sorting(repository._base_query(), params)


def test_sorting_accepts_an_allowlisted_field(
    repository: UserRepository, tenant_id: uuid.UUID
) -> None:
    set_tenant_id(tenant_id)
    params = ListQueryParams(sort_by="email", sort_dir=SortDirection.ASC)

    sql = _compile(repository._apply_sorting(repository._base_query(), params))

    assert "ORDER BY users.email ASC" in sql
    # Tie-break on the primary key keeps pagination stable across pages.
    assert "users.id DESC" in sql


def test_search_escapes_like_metacharacters(
    repository: UserRepository, tenant_id: uuid.UUID
) -> None:
    """A literal % from a user must not act as a wildcard.

    Asserts on the bound parameter rather than the rendered SQL: rendering with
    ``literal_binds`` doubles both ``%`` (for the DBAPI paramstyle) and ``\\``,
    so the compiled string is a misleading surface to test against.
    """
    set_tenant_id(tenant_id)
    params = ListQueryParams(q="100%")

    query = repository._apply_search(repository._base_query(), params)
    compiled = query.compile(dialect=postgresql.dialect())
    bound_strings = [v for v in compiled.params.values() if isinstance(v, str)]

    # The user's literal % must arrive escaped, wrapped in our own wildcards.
    assert any(value == r"%100\%%" for value in bound_strings), bound_strings


def test_unknown_filter_field_is_rejected(repository: UserRepository, tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)

    with pytest.raises(ValidationError):
        repository._apply_filters(repository._base_query(), {"nonexistent": 1})


def test_user_model_carries_a_tenant_column() -> None:
    """Guard against a tenant-owned model losing its discriminator."""
    assert "tenant_id" in User.__table__.columns
    assert not User.__table__.columns["tenant_id"].nullable
