"""Tenant isolation for Phase 6 repositories."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.analytics import AnalyticsDailyRepository
from app.repositories.automation import AutomationRuleRepository, AutomationRunRepository
from app.repositories.base import TenantScopedRepository
from app.repositories.inventory import InventoryChangeRepository, InventorySyncRunRepository
from app.repositories.notification import NotificationRepository
from app.repositories.pricing import PriceChangeRepository, PricingRuleRepository
from app.repositories.store import StoreRepository

pytestmark = pytest.mark.unit

REPOSITORIES: list[tuple[type[TenantScopedRepository[object]], str]] = [
    (StoreRepository, "stores"),  # type: ignore[list-item]
    (InventorySyncRunRepository, "inventory_sync_runs"),  # type: ignore[list-item]
    (InventoryChangeRepository, "inventory_changes"),  # type: ignore[list-item]
    (PricingRuleRepository, "pricing_rules"),  # type: ignore[list-item]
    (PriceChangeRepository, "price_changes"),  # type: ignore[list-item]
    (AutomationRuleRepository, "automation_rules"),  # type: ignore[list-item]
    (AutomationRunRepository, "automation_runs"),  # type: ignore[list-item]
    (NotificationRepository, "notifications"),  # type: ignore[list-item]
    (AnalyticsDailyRepository, "analytics_daily"),  # type: ignore[list-item]
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
def test_a_missing_tenant_context_raises(repository_class: type, table: str) -> None:
    clear_context()
    with pytest.raises(MissingTenantContextError):
        repository_class(MagicMock())._base_query()
    _ = table
