"""Review findings H-1, H-3, H-4, H-5 for the pipeline bulk worker."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from celery.exceptions import Retry, SoftTimeLimitExceeded
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.pipeline_bulk import (
    PipelineBulkItemState,
    PipelineBulkRun,
    PipelineBulkRunItem,
    PipelineBulkRunStatus,
)
from app.services.pipeline_bulk import (
    MAX_PIPELINE_BULK_ITEM_ATTEMPTS,
    ClaimResult,
    PipelineBulkRunService,
)
from app.services.product_pipeline import ProductPipelineService
from app.tasks import ai as ai_tasks
from tests.integration.pipeline_bulk_harness import EnqueueRecorder, bind_queue, run_task
from tests.integration.test_pipeline_bulk_api import seed_product, seed_tenant
from tests.integration.test_pipeline_bulk_queue import start_run

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    return bind_queue(monkeypatch, db_session)


async def _run_row(db_session: AsyncSession, run_id: str) -> PipelineBulkRun:
    found = (
        await db_session.execute(
            select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(run_id))
        )
    ).scalar_one()
    await db_session.refresh(found)
    return found


async def _items(db_session: AsyncSession, run_id: str) -> dict[uuid.UUID, PipelineBulkRunItem]:
    rows = (
        (
            await db_session.execute(
                select(PipelineBulkRunItem).where(PipelineBulkRunItem.run_id == uuid.UUID(run_id))
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        await db_session.refresh(item)
    return {item.submitted_product_id: item for item in rows}


def _fail_preview_for(
    monkeypatch: pytest.MonkeyPatch, product_id: uuid.UUID, exc: Exception
) -> None:
    original = ProductPipelineService.preview

    async def preview(self: ProductPipelineService, pid: uuid.UUID, **kwargs: Any) -> Any:
        if pid == product_id:
            raise exc
        return await original(self, pid, **kwargs)

    monkeypatch.setattr(ProductPipelineService, "preview", preview)


class TestUnexpectedItemFailure:
    """H-5: one product's unexpected error fails that item, not the run."""

    async def test_unexpected_error_fails_the_item_and_the_run_continues(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        bad = await seed_product(db_session, tenant_id)
        good = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [bad.id, good.id], "h5-unexpected")
        _fail_preview_for(monkeypatch, bad.id, RuntimeError("secret internal detail"))

        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)

        items = await _items(db_session, body["id"])
        assert result["status"] == "partial"
        assert items[bad.id].state is PipelineBulkItemState.FAILED
        assert items[bad.id].error_code == "unexpected_error"
        assert "secret internal detail" not in (items[bad.id].error_message or "")
        assert "RuntimeError" in (items[bad.id].error_message or "")
        assert items[good.id].state is PipelineBulkItemState.SUCCEEDED

    async def test_an_item_out_of_attempts_is_failed_without_another_preview(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        poison = await seed_product(db_session, tenant_id)
        good = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [poison.id, good.id], "h5-cap")
        items = await _items(db_session, body["id"])
        items[poison.id].attempt_count = MAX_PIPELINE_BULK_ITEM_ATTEMPTS
        await db_session.flush()

        previewed: list[uuid.UUID] = []
        original = ProductPipelineService.preview

        async def spy(self: ProductPipelineService, pid: uuid.UUID, **kwargs: Any) -> Any:
            previewed.append(pid)
            return await original(self, pid, **kwargs)

        monkeypatch.setattr(ProductPipelineService, "preview", spy)
        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)

        items = await _items(db_session, body["id"])
        assert poison.id not in previewed
        assert items[poison.id].state is PipelineBulkItemState.FAILED
        assert items[poison.id].error_code == "item_attempts_exhausted"
        assert items[good.id].state is PipelineBulkItemState.SUCCEEDED
        assert result["status"] == "partial"


class TestTimeLimitContinuation:
    """H-3: near the time limit, progress yields to a fresh message."""

    async def test_soft_limit_after_progress_yields_the_run_back_to_pending(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        queue: EnqueueRecorder,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        first = await seed_product(db_session, tenant_id)
        second = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [first.id, second.id], "h3-yield")
        queue.calls.clear()

        original = ProductPipelineService.preview
        calls = {"n": 0}

        async def preview(self: ProductPipelineService, pid: uuid.UUID, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 2:
                raise SoftTimeLimitExceeded()
            return await original(self, pid, **kwargs)

        monkeypatch.setattr(ProductPipelineService, "preview", preview)
        continuations: list[uuid.UUID] = []
        real_publish = ai_tasks.publish_pipeline_bulk_run

        def record_publish(run_id: uuid.UUID) -> bool:
            continuations.append(run_id)
            return real_publish(run_id)

        # The task's own publish, not the route's after-commit publish.
        monkeypatch.setattr(ai_tasks, "publish_pipeline_bulk_run", record_publish)
        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)

        run = await _run_row(db_session, body["id"])
        assert result["status"] == "yielded"
        assert run.status is PipelineBulkRunStatus.PENDING
        assert run.lease_token is None
        assert run.started_at is not None
        assert run.succeeded_count == 1
        assert continuations == [uuid.UUID(body["id"])]

        # The continuation finishes the run like any pending run.
        monkeypatch.setattr(ProductPipelineService, "preview", original)
        finished = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert finished["status"] == "completed"
        assert finished["succeeded"] == 2

    async def test_soft_limit_with_no_progress_spends_the_retry_budget(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "h3-noprogress")
        _fail_preview_for(monkeypatch, product.id, SoftTimeLimitExceeded())

        with pytest.raises(Retry):
            await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)

    def test_hard_limit_is_under_rabbitmq_default_consumer_timeout(self) -> None:
        rabbitmq_default_consumer_timeout_seconds = 30 * 60
        task = ai_tasks.process_pipeline_bulk_run
        assert task.time_limit < rabbitmq_default_consumer_timeout_seconds
        assert task.soft_time_limit < task.time_limit


class TestRepublishWindow:
    """H-1: the sweep does not republish the same pending run every tick."""

    async def test_a_recently_republished_run_is_left_alone(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "h1-window")
        run = await _run_row(db_session, body["id"])
        run.created_at = datetime.now(UTC) - ai_tasks.PENDING_GRACE - timedelta(minutes=1)
        await db_session.flush()

        first_sweep = [rid for rid, _ in await ai_tasks._unpublished_runs()]
        assert uuid.UUID(body["id"]) in first_sweep
        assert await ai_tasks._republish(uuid.UUID(body["id"]), tenant_id) == "republished"
        set_tenant_id(tenant_id)

        second_sweep = [rid for rid, _ in await ai_tasks._unpublished_runs()]
        assert uuid.UUID(body["id"]) not in second_sweep

        run = await _run_row(db_session, body["id"])
        run.enqueued_at = datetime.now(UTC) - ai_tasks.REPUBLISH_AFTER - timedelta(minutes=1)
        await db_session.flush()
        third_sweep = [rid for rid, _ in await ai_tasks._unpublished_runs()]
        assert uuid.UUID(body["id"]) in third_sweep


class TestCancellationReleasesTheLease:
    """H-4 and the H-2 claim path."""

    async def test_cancelling_a_running_run_clears_its_lease(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "h4-lease")
        set_tenant_id(tenant_id)
        service = PipelineBulkRunService(db_session)
        claim = await service.claim(uuid.UUID(body["id"]), task_id="w")
        assert claim.lease is not None

        outcome = await service.cancel(uuid.UUID(body["id"]))

        assert outcome.run.status is PipelineBulkRunStatus.CANCELLED
        assert outcome.run.lease_token is None
        assert outcome.cancel_requested_at is None

    async def test_a_recorded_cancel_is_honoured_by_the_next_claim(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "h2-claim")
        set_tenant_id(tenant_id)
        service = PipelineBulkRunService(db_session)
        await service.cancel_requests.request(
            run_id=uuid.UUID(body["id"]), requested_by_user_id=None
        )
        await db_session.flush()

        claim = await service.claim(uuid.UUID(body["id"]), task_id="w")

        run = await _run_row(db_session, body["id"])
        assert claim.result is ClaimResult.NOT_CLAIMABLE
        assert run.status is PipelineBulkRunStatus.CANCELLED

    async def test_cancel_route_reports_the_request(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "h2-route")

        response = await client.post(
            f"/api/v1/products/pipeline/runs/{body['id']}/cancel", headers=headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "cancelled"
        assert "cancelRequestedAt" in response.json()
