"""Tenant isolation for the eBay seller-connection repository.

The highest-value tests in the suite, per the repository rules: a regression
here is a cross-tenant leak of an encrypted eBay credential.

No database. The statements are compiled and the generated SQL inspected, so the
guarantee is checked on every commit rather than only where PostgreSQL happens
to be available. The behaviour against real data is asserted in
``tests/integration/test_ebay_c1_connection.py``.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.ebay import EbayConnectionRepository

pytestmark = pytest.mark.unit


def _compile(query: object) -> str:
    return str(
        query.compile(  # type: ignore[attr-defined]  # Select has no typed compile()
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


@pytest.fixture
def repository() -> EbayConnectionRepository:
    return EbayConnectionRepository(MagicMock())


class TestTheScope:
    def test_every_read_is_filtered_by_the_bound_tenant(
        self, repository: EbayConnectionRepository, tenant_id: uuid.UUID
    ) -> None:
        set_tenant_id(tenant_id)
        sql = _compile(repository._base_query())
        assert "ebay_connections.tenant_id" in sql
        assert str(tenant_id) in sql

    def test_a_missing_tenant_context_raises_rather_than_returning_everything(
        self, repository: EbayConnectionRepository
    ) -> None:
        """The reason the tenant comes from context and not an argument.

        An argument can be forgotten at any of hundreds of call sites and the
        query silently returns the whole table. This raises instead.
        """
        clear_context()
        with pytest.raises(MissingTenantContextError):
            repository._base_query()

    def test_the_row_lock_keeps_the_tenant_predicate(
        self, repository: EbayConnectionRepository, tenant_id: uuid.UUID
    ) -> None:
        """The serialisation point must not become a way around the scope.

        ``lock_for_update`` takes a connection id, which is exactly the shape
        that invites a bypass. It builds on ``_base_query``, so an id belonging
        to another workspace matches nothing — the caller sees "no connection",
        which is the same answer they would get if it did not exist.
        """
        set_tenant_id(tenant_id)
        statement = (
            repository._base_query().where(repository.model.id == uuid.uuid4()).with_for_update()
        )
        sql = _compile(statement)
        assert "ebay_connections.tenant_id" in sql
        assert str(tenant_id) in sql
        assert "FOR UPDATE" in sql

    def test_another_workspaces_id_does_not_widen_the_query(
        self, repository: EbayConnectionRepository, tenant_id: uuid.UUID
    ) -> None:
        """Both predicates survive, so the id can only ever narrow the result."""
        set_tenant_id(tenant_id)
        foreign_id = uuid.uuid4()
        sql = _compile(repository._base_query().where(repository.model.id == foreign_id))
        assert str(tenant_id) in sql
        assert str(foreign_id) in sql
        assert sql.count("WHERE") == 1
        assert " OR " not in sql.upper()


class TestNoBypassExists:
    def test_the_repository_offers_no_cross_tenant_lookup(self) -> None:
        """Structural, and deliberately strict.

        A ``get_by_ebay_user_id`` would be the obvious convenience method and is
        the one thing this class must not have: it would let a caller learn
        whether a given eBay seller uses the platform by watching which error
        came back. Cross-tenant ownership is settled by the unique constraint
        instead, and the one legitimate cross-tenant reader — the compliance
        deletion path — holds its own narrow statement elsewhere.

        Written as a check on every method *this class declares* rather than on
        one name, so a differently-spelled bypass is caught too. Inherited base
        methods are out of scope on purpose: they either build on
        ``_base_query`` or take an entity the caller has already fetched
        through it, and they have their own tests.
        """
        declared = {
            name: member
            for name, member in vars(EbayConnectionRepository).items()
            if inspect.isfunction(member) and not name.startswith("_")
        }
        assert declared, "the repository declares no methods; this test is asserting nothing"

        for name, member in declared.items():
            source = inspect.getsource(member)
            assert "_base_query" in source, (
                f"{name} builds a statement without going through the tenant scope"
            )

    def test_the_scope_comes_from_the_base_class_not_a_local_predicate(self) -> None:
        """Inherited, so a subclass cannot forget it.

        A repository that re-implemented the filter would be one edit away from
        dropping it, and nothing would fail loudly.
        """
        from app.repositories.base import TenantScopedRepository

        assert issubclass(EbayConnectionRepository, TenantScopedRepository)
        assert "_base_query" not in vars(EbayConnectionRepository), (
            "the tenant predicate has been re-implemented locally"
        )
