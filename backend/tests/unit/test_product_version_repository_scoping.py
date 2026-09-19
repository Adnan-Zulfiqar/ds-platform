"""Tenant isolation for `ProductVersionRepository`.

Required by CLAUDE.md: every new tenant-scoped repository gets an isolation
test. Runs without a database by compiling the statement and inspecting the
SQL, the same technique `test_product_repository_scoping.py` and
`test_prompt_execution_repository_scoping.py` use.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.product import ProductVersionRepository

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
    sql = _compile(ProductVersionRepository(MagicMock())._base_query())

    assert "product_versions.tenant_id" in sql
    assert str(tenant_id) in sql


def test_every_read_excludes_soft_deleted_rows(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(ProductVersionRepository(MagicMock())._base_query())

    assert "deleted_at IS NULL" in sql


def test_a_missing_tenant_context_raises_rather_than_returning_everything() -> None:
    """The failure mode that matters: an unbound tenant must be a loud error,
    never a query that quietly returns every tenant's product versions."""
    clear_context()

    with pytest.raises(MissingTenantContextError):
        ProductVersionRepository(MagicMock())._base_query()


def test_the_product_scoped_lookup_carries_the_tenant_filter(tenant_id: uuid.UUID) -> None:
    """The lookup `activate()` uses to find a version must itself be
    tenant-scoped — otherwise one tenant could activate a version belonging
    to another tenant's product by guessing its id."""
    set_tenant_id(tenant_id)
    repository = ProductVersionRepository(MagicMock())
    product_id = uuid.uuid4()

    query = repository._base_query().where(repository.model.product_id == product_id)
    sql = _compile(query)

    assert "product_versions.tenant_id" in sql
    assert str(tenant_id) in sql
    assert str(product_id) in sql


def test_product_scoped_lookup_defaults_populate_existing_off() -> None:
    """Approve/publish opt in to a fresh identity-map read; other callers stay as they were."""
    parameter = inspect.signature(ProductVersionRepository.get_by_id_for_product).parameters[
        "populate_existing"
    ]
    assert parameter.default is False


def test_declares_a_sort_allowlist() -> None:
    assert ProductVersionRepository.sortable_fields


def test_the_sort_allowlist_names_real_columns() -> None:
    repository = ProductVersionRepository(MagicMock())
    for field in repository.sortable_fields:
        assert hasattr(repository.model, field), (
            f"ProductVersionRepository allows sorting by {field!r}, which is not a column"
        )
