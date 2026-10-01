"""Lease fencing: reclaim cannot steal an in-flight successful preview."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.context import set_tenant_id
from app.models.pipeline_bulk import PipelineBulkRun, PipelineBulkRunStatus
from app.services.pipeline_bulk import (
    STALE_AFTER,
    ItemStep,
    LeaseObservation,
    PipelineBulkRunService,
)
from app.services.product_pipeline import PipelinePreview, ProductPipelineService
from tests.integration.pipeline_bulk_live import backend_pid, live_bulk, wait_until_blocked

pytestmark = pytest.mark.integration


async def _observe(factory, run_id):  # type: ignore[no-untyped-def]
    async with factory() as session:
        run = (
            await session.execute(select(PipelineBulkRun).where(PipelineBulkRun.id == run_id))
        ).scalar_one()
        return LeaseObservation(
            run_id=run.id,
            tenant_id=run.tenant_id,
            heartbeat_at=run.heartbeat_at,
            lease_token=run.lease_token,
            recovery_count=run.recovery_count,
        )


class TestFencing:
    async def test_in_flight_txn_b_blocks_reclaim(self) -> None:
        async with live_bulk(products=1, idempotency_key="fence-a") as live:
            assert live.run_id is not None
            set_tenant_id(live.tenant_id)
            async with live.session_factory() as session:
                claim = await PipelineBulkRunService(session).claim(live.run_id, task_id="worker-a")
                await session.commit()
            assert claim.lease is not None
            lease = claim.lease

            async with live.session_factory() as session:
                attempt = await PipelineBulkRunService(session).claim_attempt(
                    live.run_id, lease=lease
                )
                await session.commit()
            assert attempt.item_id is not None

            async with live.session_factory() as session:
                run = (
                    await session.execute(
                        select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                    )
                ).scalar_one()
                run.heartbeat_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1)
                await session.commit()

            observed = await _observe(live.session_factory, live.run_id)
            started = asyncio.Event()
            release = asyncio.Event()
            original = ProductPipelineService.preview

            async def hang(
                self: ProductPipelineService, *args: object, **kwargs: object
            ) -> PipelinePreview:
                started.set()
                await release.wait()
                return await original(self, *args, **kwargs)

            ProductPipelineService.preview = hang  # type: ignore[method-assign]
            try:
                async with live.session_factory() as worker:
                    set_tenant_id(live.tenant_id)

                    async def txn_b() -> ItemStep:
                        set_tenant_id(live.tenant_id)
                        return await PipelineBulkRunService(worker).process_item_success(
                            live.run_id, attempt.item_id, lease=lease
                        )

                    task = asyncio.create_task(txn_b())
                    await asyncio.wait_for(started.wait(), timeout=10)

                    async with live.session_factory() as recon:
                        set_tenant_id(live.tenant_id)
                        pid = await backend_pid(recon)

                        async def reclaim() -> bool:
                            return await PipelineBulkRunService(recon).reclaim_stale(
                                observed=observed
                            )

                        reclaim_task = asyncio.create_task(reclaim())
                        await wait_until_blocked(live.session_factory, pid, what="reclaim")
                        release.set()
                        step = await task
                        await worker.commit()
                        reclaimed = await reclaim_task
                    assert step is ItemStep.SUCCEEDED
                    assert reclaimed is False
            finally:
                ProductPipelineService.preview = original  # type: ignore[method-assign]

    async def test_reclaim_first_prevents_old_worker_preview(self) -> None:
        async with live_bulk(products=1, idempotency_key="fence-b") as live:
            assert live.run_id is not None
            set_tenant_id(live.tenant_id)
            async with live.session_factory() as session:
                claim = await PipelineBulkRunService(session).claim(live.run_id, task_id="old")
                await session.commit()
            assert claim.lease is not None
            old_lease = claim.lease

            async with live.session_factory() as session:
                run = (
                    await session.execute(
                        select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                    )
                ).scalar_one()
                run.heartbeat_at = datetime.now(UTC) - STALE_AFTER - timedelta(minutes=1)
                await session.commit()

            observed = await _observe(live.session_factory, live.run_id)
            async with live.session_factory() as session:
                set_tenant_id(live.tenant_id)
                assert await PipelineBulkRunService(session).reclaim_stale(observed=observed)
                await session.commit()

            called = {"preview": False}

            async def boom(self: ProductPipelineService, *args: object, **kwargs: object) -> None:
                called["preview"] = True
                raise AssertionError("old worker must not call preview")

            original = ProductPipelineService.preview
            ProductPipelineService.preview = boom  # type: ignore[method-assign]
            try:
                async with live.session_factory() as session:
                    set_tenant_id(live.tenant_id)
                    step = await PipelineBulkRunService(session).process_item_success(
                        live.run_id, uuid.uuid4(), lease=old_lease
                    )
                    await session.commit()
                assert step is ItemStep.LOST
                assert called["preview"] is False
            finally:
                ProductPipelineService.preview = original  # type: ignore[method-assign]

    async def test_cancel_does_not_wait_for_txn_b_and_stops_the_next_item(self) -> None:
        """Review finding H-2. Cancel used to queue behind the worker's run
        lock for a whole item. It now records a request and returns at once;
        the worker honours it at the next item boundary."""
        async with live_bulk(products=2, idempotency_key="fence-c") as live:
            assert live.run_id is not None
            set_tenant_id(live.tenant_id)
            async with live.session_factory() as session:
                claim = await PipelineBulkRunService(session).claim(live.run_id, task_id="w")
                await session.commit()
            assert claim.lease is not None
            lease = claim.lease

            async with live.session_factory() as session:
                attempt = await PipelineBulkRunService(session).claim_attempt(
                    live.run_id, lease=lease
                )
                await session.commit()
            assert attempt.item_id is not None

            started = asyncio.Event()
            release = asyncio.Event()
            original = ProductPipelineService.preview

            async def hang(
                self: ProductPipelineService, *args: object, **kwargs: object
            ) -> PipelinePreview:
                started.set()
                await release.wait()
                return await original(self, *args, **kwargs)

            ProductPipelineService.preview = hang  # type: ignore[method-assign]
            try:
                async with live.session_factory() as worker:
                    set_tenant_id(live.tenant_id)

                    async def txn_b() -> ItemStep:
                        set_tenant_id(live.tenant_id)
                        return await PipelineBulkRunService(worker).process_item_success(
                            live.run_id, attempt.item_id, lease=lease
                        )

                    task = asyncio.create_task(txn_b())
                    await asyncio.wait_for(started.wait(), timeout=10)

                    async with live.session_factory() as canceller:
                        set_tenant_id(live.tenant_id)
                        # Must return while Txn B still holds the run row.
                        outcome = await asyncio.wait_for(
                            PipelineBulkRunService(canceller).cancel(live.run_id), timeout=5
                        )
                        await canceller.commit()
                    assert task.done() is False, "cancel returned only after the worker"
                    assert outcome.run.status is PipelineBulkRunStatus.RUNNING
                    assert outcome.cancel_requested_at is not None

                    release.set()
                    step = await task
                    await worker.commit()
                    assert step is ItemStep.SUCCEEDED

                    async with live.session_factory() as session:
                        set_tenant_id(live.tenant_id)
                        next_step = await PipelineBulkRunService(session).claim_attempt(
                            live.run_id, lease=lease
                        )
                        await session.commit()
                    assert next_step.step is ItemStep.CANCELLED

                    async with live.session_factory() as session:
                        run = (
                            await session.execute(
                                select(PipelineBulkRun).where(PipelineBulkRun.id == live.run_id)
                            )
                        ).scalar_one()
                    assert run.status is PipelineBulkRunStatus.CANCELLED
                    assert run.lease_token is None
                    assert run.succeeded_count == 1

                    async with live.session_factory() as session:
                        set_tenant_id(live.tenant_id)
                        after = await PipelineBulkRunService(session).claim_attempt(
                            live.run_id, lease=lease
                        )
                    assert after.step is ItemStep.LOST
            finally:
                ProductPipelineService.preview = original  # type: ignore[method-assign]
