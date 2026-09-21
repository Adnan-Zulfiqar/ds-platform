"""Tenant isolation for pipeline bulk run repositories.

Compiled-SQL inspection, no database. Required for every new tenant-scoped
repository.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.base import TenantScopedRepository
from app.repositories.pipeline_bulk import (
    PipelineBulkRunItemRepository,
    PipelineBulkRunRepository,
)

pytestmark = pytest.mark.unit

REPOSITORIES: list[tuple[type[TenantScopedRepository[object]], str]] = [
    (PipelineBulkRunRepository, "pipeline_bulk_runs"),  # type: ignore[list-item]
    (PipelineBulkRunItemRepository, "pipeline_bulk_run_items"),  # type: ignore[list-item]
]


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.mark.parametrize(("repository_class", "table"), REPOSITORIES)
def test_every_read_is_filtered_by_the_bound_tenant(
    repository_class: type, table: str, tenant_id: uuid.UUID
) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(repository_class(MagicMock())._base_query())

    assert f"{table}.tenant_id" in sql
    assert str(tenant_id) in sql


@pytest.mark.parametrize(("repository_class", "table"), REPOSITORIES)
def test_every_read_excludes_soft_deleted_rows(
    repository_class: type, table: str, tenant_id: uuid.UUID
) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(repository_class(MagicMock())._base_query())

    assert "deleted_at IS NULL" in sql


@pytest.mark.parametrize(("repository_class", "table"), REPOSITORIES)
def test_a_missing_tenant_context_raises_rather_than_returning_everything(
    repository_class: type, table: str
) -> None:
    clear_context()
    with pytest.raises(MissingTenantContextError):
        repository_class(MagicMock())._base_query()
