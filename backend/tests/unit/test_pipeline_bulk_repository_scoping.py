"""Tenant isolation for the SQL moved out of the bulk service (A-2) and for
the version-number query (B-1).

No database: each repository method runs against a session double that
records the statement it was given, and the compiled SQL is inspected.
Behaviour against real rows is covered by the Stage 9 integration suites.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.repositories.pipeline_bulk import (
    PipelineBulkRunCancelRequestRepository,
    PipelineBulkRunItemRepository,
    PipelineBulkRunRepository,
    RunLeaseRow,
)
from app.repositories.product import ProductVersionRepository

pytestmark = pytest.mark.unit


class _RecordingSession:
    """Just enough of AsyncSession to capture what a repository executes."""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.first.return_value = None
        result.scalars.return_value.all.return_value = []
        result.tuples.return_value.first.return_value = None
        result.all.return_value = []
        return result


def _sql(statement: Any) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


@pytest.fixture
def session() -> _RecordingSession:
    return _RecordingSession()


@pytest.fixture
def tenant() -> Iterator[uuid.UUID]:
    tenant_id = uuid.uuid4()
    set_tenant_id(tenant_id)
    yield tenant_id
    clear_context()


class TestVersionNumberIsTenantScoped:
    async def test_the_max_query_carries_the_tenant_predicate(
        self, session: _RecordingSession, tenant: uuid.UUID
    ) -> None:
        await ProductVersionRepository(session).next_version_number(uuid.uuid4())  # type: ignore[arg-type]
        sql = _sql(session.statements[-1])
        assert "product_versions.tenant_id" in sql
        assert str(tenant) in sql

    async def test_no_tenant_context_raises_instead_of_counting_everything(
        self, session: _RecordingSession
    ) -> None:
        clear_context()
        with pytest.raises(MissingTenantContextError):
            await ProductVersionRepository(session).next_version_number(uuid.uuid4())  # type: ignore[arg-type]


class TestBulkRepositoriesAreTenantScoped:
    @pytest.mark.parametrize(
        "call",
        [
            lambda repo: repo.lock(uuid.uuid4()),
            lambda repo: repo.find_by_idempotency_key("k"),
            lambda repo: repo.observe_lease(uuid.uuid4()),
        ],
    )
    async def test_run_repository_reads(
        self, session: _RecordingSession, tenant: uuid.UUID, call: Any
    ) -> None:
        await call(PipelineBulkRunRepository(session))  # type: ignore[arg-type]
        sql = _sql(session.statements[-1])
        assert "pipeline_bulk_runs.tenant_id" in sql
        assert str(tenant) in sql

    async def test_owner_conditional_update_is_scoped(
        self, session: _RecordingSession, tenant: uuid.UUID
    ) -> None:
        observed = RunLeaseRow(uuid.uuid4(), tenant, None, None, 0)
        await PipelineBulkRunRepository(session).conditional_transition(  # type: ignore[arg-type]
            observed=observed,
            cutoff=datetime.now(UTC),
            below_recovery_ceiling=3,
            at_or_above_recovery_ceiling=None,
            values={"lease_token": None},
        )
        sql = _sql(session.statements[-1])
        assert "pipeline_bulk_runs.tenant_id" in sql
        assert str(tenant) in sql

    @pytest.mark.parametrize(
        "call",
        [
            lambda repo: repo.lock_next_pending(uuid.uuid4()),
            lambda repo: repo.lock_by_id(uuid.uuid4()),
            lambda repo: repo.lock_pending(uuid.uuid4()),
            lambda repo: repo.count_by_state(uuid.uuid4()),
        ],
    )
    async def test_item_repository_reads(
        self, session: _RecordingSession, tenant: uuid.UUID, call: Any
    ) -> None:
        await call(PipelineBulkRunItemRepository(session))  # type: ignore[arg-type]
        sql = _sql(session.statements[-1])
        assert "pipeline_bulk_run_items.tenant_id" in sql
        assert str(tenant) in sql

    async def test_cancel_request_insert_and_read_are_scoped(
        self, session: _RecordingSession, tenant: uuid.UUID
    ) -> None:
        repo = PipelineBulkRunCancelRequestRepository(session)  # type: ignore[arg-type]
        await repo.request(run_id=uuid.uuid4(), requested_by_user_id=None)
        insert_sql = _sql(session.statements[-1])
        await repo.requested_at(uuid.uuid4())
        read_sql = _sql(session.statements[-1])
        assert str(tenant) in insert_sql
        assert "ON CONFLICT" in insert_sql
        assert "pipeline_bulk_run_cancel_requests.tenant_id" in read_sql
        assert str(tenant) in read_sql
