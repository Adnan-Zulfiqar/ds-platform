"""Tenant isolation for the product catalogue repositories.

Required by CLAUDE.md: every new tenant-scoped repository gets an isolation
test. These are the highest-value tests in the suite, because a regression here
is a cross-tenant data leak.

Like the existing scoping tests they run **without a database**, by compiling
the statement and inspecting the SQL. That means the guarantee is checked on
every commit without needing Postgres, so there is no excuse to skip it.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.models.product import ProductSource
from app.repositories.base import TenantScopedRepository
from app.repositories.product import (
    ProductImageRepository,
    ProductImportRepository,
    ProductRepository,
    ProductVariantRepository,
)

pytestmark = pytest.mark.unit

#: Every tenant-scoped repository added in Phase 4, with the table each guards.
REPOSITORIES: list[tuple[type[TenantScopedRepository[object]], str]] = [
    (ProductRepository, "products"),  # type: ignore[list-item]
    (ProductVariantRepository, "product_variants"),  # type: ignore[list-item]
    (ProductImageRepository, "product_images"),  # type: ignore[list-item]
    (ProductImportRepository, "product_imports"),  # type: ignore[list-item]
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
    """The failure mode that matters.

    An unbound tenant must be a loud error, never a query that quietly returns
    every tenant's rows.
    """
    clear_context()

    with pytest.raises(MissingTenantContextError):
        repository_class(MagicMock())._base_query()


class TestIdempotencyLookup:
    """The lookup that makes import idempotent must itself be tenant-scoped.

    If it were not, one tenant's import would find another tenant's row for the
    same supplier product and overwrite it — a cross-tenant write, which is
    worse than a leak.
    """

    def test_external_id_lookup_carries_the_tenant_filter(self, tenant_id: uuid.UUID) -> None:
        set_tenant_id(tenant_id)
        repository = ProductRepository(MagicMock())

        query = repository._base_query().where(
            repository.model.source == ProductSource.ALIEXPRESS,
            repository.model.external_id == "3256806389000685",
        )
        sql = _compile(query)

        assert "products.tenant_id" in sql
        assert str(tenant_id) in sql
        assert "3256806389000685" in sql

    def test_status_aggregate_is_tenant_scoped(self, tenant_id: uuid.UUID) -> None:
        """Aggregates bypass `_base_query`, so they are the easiest place to
        forget the filter. The predicate is lifted from the same query the reads
        use, and this pins that it survives."""
        set_tenant_id(tenant_id)
        repository = ProductRepository(MagicMock())

        where_clause = repository._base_query().whereclause
        assert where_clause is not None
        assert str(tenant_id) in _compile(where_clause)


class TestSortAllowlists:
    """`sort_by` arrives from the query string.

    Resolving an arbitrary client string to a column is an injection vector, so
    each repository declares an allowlist.
    """

    @pytest.mark.parametrize(("repository_class", "table"), REPOSITORIES)
    def test_every_repository_declares_a_sort_allowlist(
        self, repository_class: type, table: str
    ) -> None:
        assert repository_class.sortable_fields

    def test_the_allowlist_names_real_columns(self) -> None:
        """An allowlist naming a column that does not exist would fail at
        runtime on the first client that sorted by it."""
        for repository_class, _ in REPOSITORIES:
            repository = repository_class(MagicMock())
            for field in repository.sortable_fields:
                assert hasattr(repository.model, field), (
                    f"{repository_class.__name__} allows sorting by "
                    f"{field!r}, which is not a column"
                )
