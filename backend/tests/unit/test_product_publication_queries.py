"""SQL shape for Drafts vs Products publication projections.

Product Workspace V2 Stage 0. These compile without a database so the EXISTS /
NOT EXISTS predicates cannot silently lose the tenant filter.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import set_tenant_id
from app.repositories.product import ProductRepository

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class TestPublicationPredicates:
    def test_published_predicate_requires_synced_listing(self, tenant_id: uuid.UUID) -> None:
        set_tenant_id(tenant_id)
        repository = ProductRepository(MagicMock())
        sql = _compile(
            repository._base_query().where(repository._publication_predicate("published"))
        )

        assert "products.tenant_id" in sql
        assert str(tenant_id) in sql
        assert "store_listings" in sql
        assert "synced" in sql

    def test_draft_predicate_excludes_synced_listings(self, tenant_id: uuid.UUID) -> None:
        set_tenant_id(tenant_id)
        repository = ProductRepository(MagicMock())
        sql = _compile(repository._base_query().where(repository._publication_predicate("draft")))

        assert "products.tenant_id" in sql
        assert "store_listings" in sql
        assert "NOT EXISTS" in sql.upper() or "exists" in sql.lower()
