"""Recovery, unpublished pending, stale reclaim, and recovery ceiling."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.database.session import transaction as db_transaction
from app.models.pipeline_bulk import PipelineBulkRun, PipelineBulkRunStatus
from app.services.pipeline_bulk import (
    MAX_PIPELINE_BULK_RECOVERIES,
    STALE_AFTER,
    LeaseObservation,
    PipelineBulkRunService,
)
from app.tasks import ai as ai_tasks
from tests.integration.pipeline_bulk_harness import EnqueueRecorder, bind_queue, run_task
from tests.integration.pipeline_bulk_live import live_bulk
from tests.integration.test_pipeline_bulk_api import seed_product, seed_tenant
from tests.integration.test_pipeline_bulk_queue import start_run

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    return bind_queue(monkeypatch, db_session)


async def row(db_session: AsyncSession, run_id: str) -> PipelineBulkRun:
    found = (
        await db_session.execute(
            select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(run_id))
        )
    ).scalar_one()
    await db_session.refresh(found)
    return found


class TestRecovery:
    async def test_unpublished_pending_is_republished(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        queue: EnqueueRecorder,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "unpublished")
        run = await row(db_session, body["id"])
        run.created_at = datetime.now(UTC) - ai_tasks.PENDING_GRACE - timedelta(minutes=1)
        await db_session.flush()
        await db_session.commit()
        queue.calls.clear()
        pending = await ai_tasks._unpublished_runs()
        assert uuid.UUID(body["id"]) in [identifier for identifier, _ in pending]
        outcome = await ai_tasks._republish(uuid.UUID(body["id"]), tenant_id)
        set_tenant_id(tenant_id)
        assert outcome == "republished"
        assert queue.run_ids == [body["id"]]

    async def test_stale_running_is_reclaimed(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        queue: EnqueueRecorder,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "stale")
        await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        # Force a running abandoned state after success would complete; claim then age it.
        run = await row(db_session, body["id"])
        if run.status is not PipelineBulkRunStatus.RUNNING:
            run.status = PipelineBulkRunStatus.RUNNING
            run.lease_token = uuid.uuid4()
            run.claimed_by_task_id = "dead-worker"
            run.finished_at = None
        run.heartbeat_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1)
        run.recovery_count = 0
        await db_session.flush()

        observed = LeaseObservation(
            run_id=run.id,
            tenant_id=tenant_id,
            heartbeat_at=run.heartbeat_at,
            lease_token=run.lease_token,
            recovery_count=run.recovery_count,
        )
        queue.calls.clear()
        outcome = await ai_tasks._reconcile_one(observed)
        set_tenant_id(tenant_id)
        assert outcome == "requeued"
        reclaimed = await row(db_session, body["id"])
        assert reclaimed.status is PipelineBulkRunStatus.PENDING
        assert reclaimed.lease_token is None
        assert reclaimed.recovery_count == 1
        assert queue.run_ids == [body["id"]]

    async def test_recovery_ceiling_parks_the_run(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "ceiling")
        run = await row(db_session, body["id"])
        run.status = PipelineBulkRunStatus.RUNNING
        run.lease_token = uuid.uuid4()
        run.claimed_by_task_id = "dead"
        run.heartbeat_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1)
        run.recovery_count = MAX_PIPELINE_BULK_RECOVERIES
        await db_session.flush()
        observed = LeaseObservation(
            run_id=run.id,
            tenant_id=tenant_id,
            heartbeat_at=run.heartbeat_at,
            lease_token=run.lease_token,
            recovery_count=run.recovery_count,
        )
        outcome = await ai_tasks._reconcile_one(observed)
        set_tenant_id(tenant_id)
        assert outcome == "abandoned"
        parked = await row(db_session, body["id"])
        assert parked.status is PipelineBulkRunStatus.FAILED
        assert parked.lease_token is None

    async def test_reclaim_preserves_first_started_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ai_tasks, "transaction", db_transaction)
        async with live_bulk(products=1, idempotency_key="started-at") as live:
            assert live.run_id is not None
            set_tenant_id(live.tenant_id)
            async with live.session_factory() as session:
                first = await PipelineBulkRunService(session).claim(live.run_id, task_id="worker-a")
                await session.commit()
            assert first.lease is not None
            first_lease = first.lease.token

            async with live.session_factory() as session:
                run = (
                    await session.execute(
                        select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                    )
                ).scalar_one()
                first_started_at = run.started_at
                assert first_started_at is not None
                assert run.claimed_by_task_id == "worker-a"
                run.heartbeat_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1)
                await session.commit()

            async with live.session_factory() as session:
                aged = (
                    await session.execute(
                        select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                    )
                ).scalar_one()
                observed = LeaseObservation(
                    run_id=aged.id,
                    tenant_id=live.tenant_id,
                    heartbeat_at=aged.heartbeat_at,
                    lease_token=aged.lease_token,
                    recovery_count=aged.recovery_count,
                )

            outcome = await ai_tasks._reconcile_one(observed)
            assert outcome == "requeued"

            set_tenant_id(live.tenant_id)
            async with live.session_factory() as session:
                reclaimed = (
                    await session.execute(
                        select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                    )
                ).scalar_one()
                assert reclaimed.status is PipelineBulkRunStatus.PENDING
                assert reclaimed.started_at == first_started_at
                assert reclaimed.recovery_count == 1
                second = await PipelineBulkRunService(session).claim(
                    live.run_id, task_id="worker-b"
                )
                await session.commit()
            assert second.lease is not None
            assert second.lease.token != first_lease

            async with live.session_factory() as session:
                after = (
                    await session.execute(
                        select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                    )
                ).scalar_one()
                assert after.status is PipelineBulkRunStatus.RUNNING
                assert after.started_at == first_started_at
                assert after.claimed_by_task_id == "worker-b"
                assert after.lease_token != first_lease
                assert after.heartbeat_at is not None
                assert after.heartbeat_at > first_started_at
                assert after.recovery_count == 1
