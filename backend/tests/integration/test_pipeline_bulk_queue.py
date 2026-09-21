"""Worker execution for pipeline bulk runs: preview-only, tenancy, missing ids."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from celery.exceptions import Retry
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions import AIError, AIProviderNotConfiguredError
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.pipeline_bulk import PipelineBulkItemState, PipelineBulkRun, PipelineBulkRunItem
from app.models.product import (
    ProductStatus,
    ProductVersion,
    ProductVersionSource,
)
from app.repositories.product import ProductVersionRepository
from app.services.pipeline_bulk import ClaimResult
from app.services.product_pipeline import ProductPipelineService
from app.tasks import ai as ai_tasks
from tests.integration.pipeline_bulk_harness import EnqueueRecorder, bind_queue, run_task
from tests.integration.test_pipeline_bulk_api import RUNS, seed_product, seed_store, seed_tenant

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> EnqueueRecorder:
    return bind_queue(monkeypatch, db_session)


async def start_run(
    client: AsyncClient, headers: dict[str, str], product_ids: list[uuid.UUID], key: str
) -> dict[str, Any]:
    response = await client.post(
        RUNS,
        json={"productIds": [str(pid) for pid in product_ids], "idempotencyKey": key},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


class TestQueueExecution:
    async def test_successful_run_creates_one_inactive_pipeline_candidate(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "ok-run")

        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert result["status"] == "completed"
        assert result["succeeded"] == 1

        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                )
            )
        ).scalar_one()
        assert item.state is PipelineBulkItemState.SUCCEEDED
        assert item.product_id == product.id
        assert item.candidate_version_id is not None

        versions = (
            (
                await db_session.execute(
                    select(ProductVersion).where(ProductVersion.product_id == product.id)
                )
            )
            .scalars()
            .all()
        )
        pipeline = [
            version
            for version in versions
            if version.source is ProductVersionSource.AI_GENERATED
            and isinstance(version.content, dict)
            and version.content.get("pipelineCandidateVersion") == 1
        ]
        assert len(pipeline) == 1
        assert pipeline[0].id == item.candidate_version_id
        await db_session.refresh(product)
        from app.models.product import ProductAIStatus

        assert product.ai_status is ProductAIStatus.NOT_OPTIMIZED

    async def test_foreign_product_ends_missing_with_null_product_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        own = await seed_product(db_session, tenant_id)
        foreign = uuid.uuid4()
        body = await start_run(client, headers, [own.id, foreign], "mixed")

        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert result["status"] == "partial"
        assert result["succeeded"] == 1
        assert result["missing"] == 1

        items = (
            (
                await db_session.execute(
                    select(PipelineBulkRunItem).where(
                        PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        by_submitted = {item.submitted_product_id: item for item in items}
        assert by_submitted[own.id].state is PipelineBulkItemState.SUCCEEDED
        assert by_submitted[own.id].product_id == own.id
        assert by_submitted[foreign].state is PipelineBulkItemState.MISSING
        assert by_submitted[foreign].product_id is None
        assert by_submitted[foreign].candidate_version_id is None

    async def test_archived_product_is_skipped(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        product.status = ProductStatus.ARCHIVED
        await db_session.flush()
        body = await start_run(client, headers, [product.id], "archived")
        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert result["status"] == "completed"
        assert result["skipped"] == 1
        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                )
            )
        ).scalar_one()
        assert item.state is PipelineBulkItemState.SKIPPED
        assert item.product_id == product.id
        assert item.error_code == "product_not_eligible"

    async def test_forged_context_tenant_is_ignored(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        owner, owner_tenant = await seed_tenant(client)
        product = await seed_product(db_session, owner_tenant)
        body = await start_run(client, owner, [product.id], "forged")
        _, intruder = await seed_tenant(client)
        assert intruder != owner_tenant
        result = await run_task(
            monkeypatch,
            run_id=body["id"],
            tenant_id=owner_tenant,
            extra_kwargs={"_context": {"tenant_id": str(intruder)}},
        )
        assert result["status"] == "completed"
        await db_session.refresh(product)
        versions = (
            await db_session.execute(
                select(func.count())
                .select_from(ProductVersion)
                .where(ProductVersion.product_id == product.id)
                .where(ProductVersion.source == ProductVersionSource.AI_GENERATED)
            )
        ).scalar_one()
        assert versions == 1

    async def test_missing_run_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = await run_task(monkeypatch, run_id=uuid.uuid4())
        assert result["status"] == ClaimResult.UNKNOWN.value

    async def test_duplicate_delivery_does_not_create_a_second_candidate(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "dup")
        first = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert first["status"] == "completed"
        second = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert second["status"] in {ClaimResult.NOT_CLAIMABLE.value, "completed"}
        pipeline = (
            (
                await db_session.execute(
                    select(ProductVersion).where(ProductVersion.product_id == product.id)
                )
            )
            .scalars()
            .all()
        )
        marked = [
            version
            for version in pipeline
            if version.source is ProductVersionSource.AI_GENERATED
            and isinstance(version.content, dict)
            and version.content.get("pipelineCandidateVersion") == 1
        ]
        assert len(marked) == 1

    async def test_provider_not_configured_fails_the_run_without_versions(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        first = await seed_product(db_session, tenant_id)
        second = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [first.id, second.id], "no-provider")

        def _raise(_settings: object) -> None:
            raise AIProviderNotConfiguredError("AI_PROVIDER=openai has no implementation yet.")

        monkeypatch.setattr(ai_tasks, "get_ai_provider", _raise)
        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert result["status"] == "failed"

        items = (
            (
                await db_session.execute(
                    select(PipelineBulkRunItem).where(
                        PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 2
        assert {item.state for item in items} == {PipelineBulkItemState.FAILED}
        assert {item.error_code for item in items} == {"ai_provider_not_configured"}
        assert all(item.candidate_version_id is None for item in items)

        run = (
            await db_session.execute(
                select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
        await db_session.refresh(run)
        assert run.status.value == "failed"
        assert run.total_count == 2
        assert run.processed_count == 2
        assert run.failed_count == 2
        assert run.succeeded_count == 0
        assert run.skipped_count == 0
        assert run.missing_count == 0
        assert run.processed_count == (
            run.succeeded_count + run.failed_count + run.skipped_count + run.missing_count
        )

        versions = (
            await db_session.execute(
                select(func.count())
                .select_from(ProductVersion)
                .where(ProductVersion.product_id.in_((first.id, second.id)))
                .where(ProductVersion.source == ProductVersionSource.AI_GENERATED)
            )
        ).scalar_one()
        assert versions == 0

    async def test_preview_ai_error_fails_the_item_without_task_retry(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "ai-error")

        async def _fail(self: ProductPipelineService, *args: object, **kwargs: object) -> None:
            raise AIError("synthetic generation failure")

        monkeypatch.setattr(ProductPipelineService, "preview", _fail)
        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert result["status"] == "failed"
        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                )
            )
        ).scalar_one()
        assert item.state is PipelineBulkItemState.FAILED
        assert item.error_code == "ai_error"
        assert item.attempt_count >= 1

    async def test_validation_error_fails_the_item(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "validation")

        async def _fail(self: ProductPipelineService, *args: object, **kwargs: object) -> None:
            raise ValidationError("bad product", details={"reason": "malformed"})

        monkeypatch.setattr(ProductPipelineService, "preview", _fail)
        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert result["status"] == "failed"
        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                )
            )
        ).scalar_one()
        assert item.state is PipelineBulkItemState.FAILED
        assert item.error_code == "validation_error"

    async def test_preview_prompt_not_found_fails_the_item_not_missing(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "prompt-missing")

        async def _fail(self: ProductPipelineService, *args: object, **kwargs: object) -> None:
            raise NotFoundError.for_resource("Prompt", "product_title_generator")

        monkeypatch.setattr(ProductPipelineService, "preview", _fail)
        result = await run_task(monkeypatch, run_id=body["id"], tenant_id=tenant_id)
        assert result["status"] == "failed"

        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                )
            )
        ).scalar_one()
        assert item.state is PipelineBulkItemState.FAILED
        assert item.error_code == "not_found"
        assert item.product_id is None
        assert item.candidate_version_id is None

        run = (
            await db_session.execute(
                select(PipelineBulkRun).where(PipelineBulkRun.id == uuid.UUID(body["id"]))
            )
        ).scalar_one()
        await db_session.refresh(run)
        assert run.failed_count == 1
        assert run.missing_count == 0
        assert run.processed_count == 1
        assert run.status.value == "failed"
        versions = (
            await db_session.execute(
                select(func.count())
                .select_from(ProductVersion)
                .where(ProductVersion.product_id == product.id)
                .where(ProductVersion.source == ProductVersionSource.AI_GENERATED)
            )
        ).scalar_one()
        assert versions == 0

    async def test_conflict_error_retries_and_creates_exactly_one_candidate(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "conflict-race")

        original = ProductVersionRepository.create
        calls = {"n": 0}

        async def flaky(self: ProductVersionRepository, **values: object) -> ProductVersion:
            if values.get("source") is ProductVersionSource.AI_GENERATED and calls["n"] == 0:
                calls["n"] = 1
                raise ConflictError("Another record with these values already exists.")
            return await original(self, **values)

        monkeypatch.setattr(ProductVersionRepository, "create", flaky)
        task_id = str(uuid.uuid4())
        with pytest.raises(Retry) as retry:
            await run_task(
                monkeypatch,
                run_id=body["id"],
                tenant_id=tenant_id,
                task_id=task_id,
                retries=0,
            )
        assert "ConflictError" in str(retry.value)

        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(body["id"])
                )
            )
        ).scalar_one()
        assert item.state is PipelineBulkItemState.PENDING
        assert item.candidate_version_id is None
        assert item.attempt_count >= 1

        result = await run_task(
            monkeypatch,
            run_id=body["id"],
            tenant_id=tenant_id,
            task_id=task_id,
            retries=1,
        )
        assert result["status"] == "completed"
        await db_session.refresh(item)
        marked = (
            (
                await db_session.execute(
                    select(ProductVersion)
                    .where(ProductVersion.product_id == product.id)
                    .where(ProductVersion.source == ProductVersionSource.AI_GENERATED)
                )
            )
            .scalars()
            .all()
        )
        pipeline = [
            version
            for version in marked
            if isinstance(version.content, dict)
            and version.content.get("pipelineCandidateVersion") == 1
        ]
        assert len(pipeline) == 1
        assert item.candidate_version_id == pipeline[0].id

    async def test_broker_payload_is_only_the_run_id(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        queue: EnqueueRecorder,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        body = await start_run(client, headers, [product.id], "payload-only")
        assert queue.calls == []
        await db_session.commit()
        assert queue.run_ids == [body["id"]]
        published = queue.calls[0]
        assert published["task"] == "ai.process_pipeline_bulk_run"
        assert set(published) == {"task", "run_id"}
        assert str(product.id) not in str(published)
        assert str(tenant_id) not in str(published)

    async def test_store_disappearance_fails_the_run_not_the_item(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        product = await seed_product(db_session, tenant_id)
        store = await seed_store(db_session, tenant_id)
        response = await client.post(
            RUNS,
            json={
                "productIds": [str(product.id)],
                "idempotencyKey": "store-gone",
                "storeId": str(store.id),
            },
            headers=headers,
        )
        assert response.status_code == 202, response.text
        store.deleted_at = datetime.now(UTC)
        await db_session.flush()
        result = await run_task(monkeypatch, run_id=response.json()["id"], tenant_id=tenant_id)
        assert result["status"] == "failed"
        item = (
            await db_session.execute(
                select(PipelineBulkRunItem).where(
                    PipelineBulkRunItem.run_id == uuid.UUID(response.json()["id"])
                )
            )
        ).scalar_one()
        assert item.state is PipelineBulkItemState.PENDING
        assert item.product_id is None
        versions = (
            await db_session.execute(
                select(func.count())
                .select_from(ProductVersion)
                .where(ProductVersion.product_id == product.id)
                .where(ProductVersion.source == ProductVersionSource.AI_GENERATED)
            )
        ).scalar_one()
        assert versions == 0
