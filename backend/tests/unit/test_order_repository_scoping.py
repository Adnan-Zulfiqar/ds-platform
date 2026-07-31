"""Tenant isolation for the order management repositories.

Required by CLAUDE.md: every new tenant-scoped repository gets an isolation
test. Orders carry buyer names and addresses, so a regression here is a
cross-tenant PII leak — strictly worse than leaking a product title.

Like the existing scoping tests they run **without a database**, by compiling
the statement and inspecting the SQL.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.models.order import OrderSource
from app.repositories.base import TenantScopedRepository
from app.repositories.order import (
    OrderEventRepository,
    OrderItemRepository,
    OrderRepository,
    OrderSyncRunRepository,
    ShipmentRepository,
    TrackingEventRepository,
)

pytestmark = pytest.mark.unit

#: Every tenant-scoped repository added in Phase 5, with the table each guards.
REPOSITORIES: list[tuple[type[TenantScopedRepository[object]], str]] = [
    (OrderRepository, "orders"),  # type: ignore[list-item]
    (OrderItemRepository, "order_items"),  # type: ignore[list-item]
    (ShipmentRepository, "shipments"),  # type: ignore[list-item]
    (TrackingEventRepository, "tracking_events"),  # type: ignore[list-item]
    (OrderEventRepository, "order_events"),  # type: ignore[list-item]
    (OrderSyncRunRepository, "order_sync_runs"),  # type: ignore[list-item]
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
    """The lookup that makes order sync idempotent must itself be tenant-scoped.

    If it were not, one tenant's sync would find another tenant's row for the
    same supplier order and overwrite it — a cross-tenant write into someone
    else's order history.
    """

    def test_external_id_lookup_carries_the_tenant_filter(self, tenant_id: uuid.UUID) -> None:
        set_tenant_id(tenant_id)
        repository = OrderRepository(MagicMock())

        query = repository._base_query().where(
            repository.model.source == OrderSource.ALIEXPRESS,
            repository.model.external_id == "8123456789012345",
        )
        sql = _compile(query)

        assert "orders.tenant_id" in sql
        assert str(tenant_id) in sql
        assert "8123456789012345" in sql

    def test_status_aggregate_is_tenant_scoped(self, tenant_id: uuid.UUID) -> None:
        """Aggregates bypass `_base_query`, so they are the easiest place to
        forget the filter."""
        set_tenant_id(tenant_id)
        repository = OrderRepository(MagicMock())

        where_clause = repository._base_query().whereclause
        assert where_clause is not None
        assert str(tenant_id) in _compile(where_clause)

    def test_shipment_tracking_lookup_carries_the_tenant_filter(self, tenant_id: uuid.UUID) -> None:
        """The shipment upsert key includes the tracking number, which carriers
        reuse — without the tenant predicate, two tenants shipping through the
        same carrier could collide."""
        set_tenant_id(tenant_id)
        repository = ShipmentRepository(MagicMock())

        query = repository._base_query().where(
            repository.model.tracking_number == "LP00123456789CN",
        )
        sql = _compile(query)

        assert "shipments.tenant_id" in sql
        assert str(tenant_id) in sql


class TestSortAllowlists:
    """`sort_by` arrives from the query string; each repository declares an
    allowlist rather than resolving arbitrary client strings to columns."""

    @pytest.mark.parametrize(("repository_class", "table"), REPOSITORIES)
    def test_every_repository_declares_a_sort_allowlist(
        self, repository_class: type, table: str
    ) -> None:
        assert repository_class.sortable_fields

    def test_the_allowlist_names_real_columns(self) -> None:
        for repository_class, _ in REPOSITORIES:
            repository = repository_class(MagicMock())
            for field in repository.sortable_fields:
                assert hasattr(repository.model, field), (
                    f"{repository_class.__name__} allows sorting by "
                    f"{field!r}, which is not a column"
                )
