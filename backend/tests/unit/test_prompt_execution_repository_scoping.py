"""Tenant isolation for `PromptExecutionRepository`.

Required by CLAUDE.md: every new tenant-scoped repository gets an isolation
test. Runs without a database by compiling the statement and inspecting the
SQL, following the same technique as
`test_product_repository_scoping.py`.

`PromptRepository` (over `AIPrompt`) is deliberately absent here — it is
platform reference data with no `tenant_id`, the same reason
`test_role_repository_scoping.py` does not exist for `RoleRepository`.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.ai_prompt import PromptExecutionRepository

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_every_read_is_filtered_by_the_bound_tenant(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(PromptExecutionRepository(MagicMock())._base_query())

    assert "prompt_executions.tenant_id" in sql
    assert str(tenant_id) in sql


def test_every_read_excludes_soft_deleted_rows(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(PromptExecutionRepository(MagicMock())._base_query())

    assert "deleted_at IS NULL" in sql


def test_a_missing_tenant_context_raises_rather_than_returning_everything() -> None:
    """The failure mode that matters: an unbound tenant must be a loud error,
    never a query that quietly returns every tenant's executions."""
    clear_context()

    with pytest.raises(MissingTenantContextError):
        PromptExecutionRepository(MagicMock())._base_query()


def test_declares_a_sort_allowlist() -> None:
    """`sort_by` arrives from the query string; resolving an arbitrary client
    string to a column without an allowlist is an injection vector."""
    assert PromptExecutionRepository.sortable_fields


def test_the_sort_allowlist_names_real_columns() -> None:
    repository = PromptExecutionRepository(MagicMock())
    for field in repository.sortable_fields:
        assert hasattr(repository.model, field), (
            f"PromptExecutionRepository allows sorting by {field!r}, which is not a column"
        )
