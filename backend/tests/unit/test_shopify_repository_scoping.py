"""Tenant isolation for Shopify repositories."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.shopify import ShopifyConnectionRepository, StoreListingRepository

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_connection_query_is_tenant_filtered(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(ShopifyConnectionRepository(MagicMock())._base_query())
    assert "shopify_connections.tenant_id" in sql
    assert str(tenant_id) in sql


def test_listing_query_is_tenant_filtered(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    sql = _compile(StoreListingRepository(MagicMock())._base_query())
    assert "store_listings.tenant_id" in sql
    assert str(tenant_id) in sql
    assert "deleted_at IS NULL" in sql


def test_missing_tenant_raises() -> None:
    clear_context()
    with pytest.raises(MissingTenantContextError):
        ShopifyConnectionRepository(MagicMock())._base_query()
