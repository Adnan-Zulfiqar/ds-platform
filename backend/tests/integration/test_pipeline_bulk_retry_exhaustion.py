"""Celery retry budget for pipeline bulk runs: 0/1/2 re-raise, 3 terminal return."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from celery.exceptions import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_bulk import PipelineBulkRun, PipelineBulkRunStatus
from app.services.pipeline_bulk import PipelineBulkLease
from app.tasks import ai as ai_tasks
from tests.integration.pipeline_bulk_harness import EnqueueRecorder, bind_queue, run_task
from tests.integration.test_pipeline_bulk_api import seed_product, seed_tenant
from tests.integration.test_pipeline_bulk_queue import start_run

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    return bind_queue(monkeypatch, db_session)


async def _force_running(
    db_session: AsyncSession, run_id: str, *, task_id: str
) -> PipelineBulkLease:
    run = (
        await db_session.execute(
            select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(run_id))
        )
    ).scalar_one()
    token = uuid.uuid4()
    run.status = PipelineBulkRunStatus.RUNNING
    run.claimed_by_task_id = task_id
    run.lease_token = token
    await db_session.flush()
    return PipelineBulkLease(run_id=run.id, task_id=task_id, token=token)


class TestRetryExhaustion:
    """Task retry mechanics — not live provider retryability."""

    async def test_retries_0_1_2_re_raise(
        self,
        client: Any,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "retry-raise")

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("synthetic task retry mechanics")

        monkeypatch.setattr(ai_tasks, "_process_pipeline_bulk_run", boom)
        for retries in (0, 1, 2):
            with pytest.raises(Retry) as retry:
                await run_task(
                    monkeypatch,
                    run_id=body["id"],
                    tenant_id=tenant_id,
                    retries=retries,
                )
            assert "synthetic task retry mechanics" in str(retry.value)

    async def test_retries_3_marks_failed_and_returns(
        self,
        client: Any,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "retry-3")
        task_id = str(uuid.uuid4())
        lease = await _force_running(db_session, body["id"], task_id=task_id)

        def boom(*args: object, **kwargs: object) -> None:
            holder = args[2]
            holder.lease = lease
            raise RuntimeError("synthetic task retry mechanics")

        monkeypatch.setattr(ai_tasks, "_process_pipeline_bulk_run", boom)
        result = await run_task(
            monkeypatch,
            run_id=body["id"],
            tenant_id=tenant_id,
            task_id=task_id,
            retries=3,
        )
        assert result["status"] == "failed"
        run = (
            await db_session.execute(
                select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
        await db_session.refresh(run)
        assert run.status is PipelineBulkRunStatus.FAILED
        assert run.failure_reason == ai_tasks.SAFE_FAILURE_REASON
        assert run.lease_token is None

    async def test_retries_3_stale_lease_writes_nothing(
        self,
        client: Any,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "stale-3")
        old_lease = await _force_running(db_session, body["id"], task_id="old")
        run = (
            await db_session.execute(
                select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
        run.lease_token = uuid.uuid4()
        run.claimed_by_task_id = "new-owner"
        await db_session.flush()

        def boom(*args: object, **kwargs: object) -> None:
            holder = args[2]
            holder.lease = old_lease
            raise RuntimeError("synthetic task retry mechanics")

        monkeypatch.setattr(ai_tasks, "_process_pipeline_bulk_run", boom)
        result = await run_task(
            monkeypatch,
            run_id=body["id"],
            tenant_id=tenant_id,
            retries=3,
        )
        assert result["status"] == "superseded"
        await db_session.refresh(run)
        assert run.status is PipelineBulkRunStatus.RUNNING
        assert run.claimed_by_task_id == "new-owner"
        assert run.failure_reason is None
