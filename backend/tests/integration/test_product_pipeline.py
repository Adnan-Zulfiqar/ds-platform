"""Integration tests for Stage 7 ProductPipelineService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.context import clear_context, set_tenant_id
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations.aliexpress import service as service_module
from app.models.ai_prompt import PromptExecution
from app.models.product import Product, ProductAIStatus, ProductVersion, ProductVersionSource
from app.repositories.product import ProductVersionRepository
from app.schemas.product import DESCRIPTION_MAX_LENGTH
from app.services.image_analysis import ImageAnalysisReport, ImageAnalysisService
from app.services.product_pipeline import (
    PIPELINE_APPROVAL_TITLE_MAX,
    PIPELINE_PUBLISH_TITLE_MAX,
    ProductPipelineService,
)
from tests.integration.test_products import (
    REAL_PRODUCT_ID,
    connected_tenant,
    id_echoing_handler,
    patch_aliexpress,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


@pytest.fixture(autouse=True)
def _skip_image_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(
        self: ImageAnalysisService,
        product_id: uuid.UUID,
        *,
        executed_by_user_id: uuid.UUID | None = None,
    ) -> ImageAnalysisReport:
        return ImageAnalysisReport(product_id=product_id, images=())

    monkeypatch.setattr(ImageAnalysisService, "analyse_product_images", _empty)


async def _import_product(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], dict[str, Any]]:
    headers = await connected_tenant(client, monkeypatch)
    response = await client.post(
        "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
    )
    assert response.status_code == 201, response.text
    return headers, response.json()


async def _bind_tenant(db_session: AsyncSession, product_id: str) -> Product:
    product = (
        await db_session.execute(select(Product).where(Product.id == uuid.UUID(product_id)))
    ).scalar_one()
    set_tenant_id(product.tenant_id)
    return product


async def _advance_product_updated_at(db_session: AsyncSession, product: Product) -> Product:
    """Move `updated_at` the way a later HTTP request would.

    Integration tests share one transaction with the app client, and Postgres
    `now()` is frozen for that transaction — the same reason M2A concurrency
    tests write `updated_at` directly instead of expecting a second flush to
    advance it.
    """
    next_token = product.updated_at + timedelta(seconds=1)
    await db_session.execute(
        update(Product).where(Product.id == product.id).values(updated_at=next_token)
    )
    await db_session.flush()
    await db_session.refresh(product)
    return product


async def _insert_pipeline_candidate(
    db_session: AsyncSession,
    product: Product,
    *,
    title: str = "Approved AI title",
    description: str = "<p>Approved body</p>",
    is_synthetic: bool = False,
    ai_provider: str | None = "test",
    extra_content: dict[str, Any] | None = None,
) -> ProductVersion:
    """A strict pipeline row. `ai_provider='test'` is a fixture, not a live model."""
    content: dict[str, Any] = {
        "title": title,
        "description": description,
        "seoTitle": "AI SEO TITLE MUST NOT PUBLISH",
        "seoDescription": "AI SEO DESC MUST NOT PUBLISH",
        "keywords": "ai,must,not,become,tags",
        "pipelineCandidateVersion": 1,
        "pipelineSourceUpdatedAt": product.updated_at.isoformat(),
        "isSynthetic": is_synthetic,
    }
    if extra_content:
        content.update(extra_content)
    versions = ProductVersionRepository(db_session)
    return await versions.create(
        product_id=product.id,
        version_number=await versions.next_version_number(product.id),
        source=ProductVersionSource.AI_GENERATED,
        content=content,
        active=False,
        ai_provider=ai_provider,
        prompt_execution_id=None,
        created_by_user_id=None,
    )


@pytest.fixture
def shopify_must_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _blocked(self: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("Shopify publisher must not be called")

    monkeypatch.setattr(
        "app.integrations.shopify.sync.ShopifySyncService.publish_product",
        _blocked,
    )


class TestPreview:
    async def test_preview_creates_an_inactive_candidate(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        await _bind_tenant(db_session, product["id"])
        try:
            preview = await ProductPipelineService(db_session).preview(
                uuid.UUID(product["id"]),
                requested_by_user_id=None,
            )
            await db_session.flush()
            versions = await ProductVersionRepository(db_session).list_for_product(
                uuid.UUID(product["id"])
            )
        finally:
            clear_context()

        assert preview.candidate_active is False
        assert preview.publishable is False
        assert any(item.code == "candidate_not_approved" for item in preview.pipeline_blockers)
        generated = next(v for v in versions if v.source is ProductVersionSource.AI_GENERATED)
        assert generated.active is False
        assert generated.content["pipelineCandidateVersion"] == 1
        assert preview.quality_baseline is not None
        assert preview.quality_baseline.version_number == 1
        assert preview.original.title == product["title"]
        assert preview.proposal.tags == ()

    async def test_preview_does_not_write_merchant_fields(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await _import_product(client, monkeypatch)
        before = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()
        await _bind_tenant(db_session, product["id"])
        try:
            await ProductPipelineService(db_session).preview(
                uuid.UUID(product["id"]),
                requested_by_user_id=None,
            )
            await db_session.flush()
        finally:
            clear_context()
        after = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()
        assert after["title"] == before["title"]
        assert after["description"] == before["description"]
        assert after["aiStatus"] == before["aiStatus"]
        assert after["seoTitle"] == before.get("seoTitle")
        assert after["tags"] == before.get("tags")

    async def test_preview_is_not_idempotent(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            first = await pipeline.preview(uuid.UUID(product["id"]), requested_by_user_id=None)
            second = await pipeline.preview(uuid.UUID(product["id"]), requested_by_user_id=None)
        finally:
            clear_context()
        assert first.candidate_version_id != second.candidate_version_id

    async def test_preview_runs_the_stage_4_prompt_trio(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        await _bind_tenant(db_session, product["id"])
        try:
            await ProductPipelineService(db_session).preview(
                uuid.UUID(product["id"]),
                requested_by_user_id=None,
            )
            await db_session.flush()
        finally:
            clear_context()
        names = (await db_session.execute(select(PromptExecution.prompt_name))).scalars().all()
        assert sorted(names) == [
            "product_description_generator",
            "product_title_generator",
            "seo_optimizer",
        ]

    async def test_get_preview_rejects_a_legacy_optimize_version(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await _import_product(client, monkeypatch)
        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        version_id = uuid.UUID(response.json()["version"]["id"])
        await _bind_tenant(db_session, product["id"])
        try:
            with pytest.raises(ValidationError) as exc_info:
                await ProductPipelineService(db_session).get_preview(
                    uuid.UUID(product["id"]),
                    version_id=version_id,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "not_a_pipeline_candidate"

    async def test_get_preview_does_not_create_another_version(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            created = await pipeline.preview(uuid.UUID(product["id"]), requested_by_user_id=None)
            await db_session.flush()
            before = await ProductVersionRepository(db_session).list_for_product(
                uuid.UUID(product["id"])
            )
            again = await pipeline.get_preview(
                uuid.UUID(product["id"]),
                version_id=created.candidate_version_id,
            )
            after = await ProductVersionRepository(db_session).list_for_product(
                uuid.UUID(product["id"])
            )
        finally:
            clear_context()
        assert len(after) == len(before)
        assert again.candidate_version_id == created.candidate_version_id
        assert again.publishable is False

    async def test_inactive_current_candidate_is_not_stale(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        await _bind_tenant(db_session, product["id"])
        try:
            preview = await ProductPipelineService(db_session).preview(
                uuid.UUID(product["id"]),
                requested_by_user_id=None,
            )
        finally:
            clear_context()
        assert all(item.code != "stale_preview" for item in preview.pipeline_blockers)

    async def test_legacy_optimize_still_auto_activates(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await _import_product(client, monkeypatch)
        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert response.status_code == 201, response.text
        assert response.json()["version"]["active"] is True


class TestApprove:
    async def test_approve_activates_the_exact_candidate(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await _import_product(client, monkeypatch)
        merchant_title = product["title"]
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            await db_session.refresh(row)
            approved = await pipeline.approve(
                row.id,
                version_id=preview.candidate_version_id,
                expected_updated_at=row.updated_at,
            )
            versions = await ProductVersionRepository(db_session).list_for_product(row.id)
            after_preview = await pipeline.get_preview(
                row.id, version_id=preview.candidate_version_id
            )
        finally:
            clear_context()

        assert approved.ai_status is ProductAIStatus.OPTIMIZED
        assert approved.ai_version == preview.candidate_version_number
        assert approved.optimized_title == preview.proposal.title
        assert approved.title == merchant_title
        assert after_preview.candidate_active is True
        assert all(item.code != "stale_preview" for item in after_preview.pipeline_blockers)
        active = next(v for v in versions if v.id == preview.candidate_version_id)
        assert active.active is True
        after = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()
        assert after["title"] == merchant_title
        assert after["seoTitle"] == product.get("seoTitle")
        assert after["tags"] == product.get("tags")

    async def test_reapprove_same_candidate_is_a_noop(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            await db_session.refresh(row)
            pre_token = row.updated_at
            first = await pipeline.approve(
                row.id,
                version_id=preview.candidate_version_id,
                expected_updated_at=pre_token,
            )
            versions_after_first = await ProductVersionRepository(db_session).list_for_product(
                row.id
            )
            retry_old = await pipeline.approve(
                row.id,
                version_id=preview.candidate_version_id,
                expected_updated_at=pre_token,
            )
            await db_session.refresh(row)
            retry_new = await pipeline.approve(
                row.id,
                version_id=preview.candidate_version_id,
                expected_updated_at=row.updated_at,
            )
            versions_after_retry = await ProductVersionRepository(db_session).list_for_product(
                row.id
            )
        finally:
            clear_context()

        assert retry_old.id == first.id
        assert retry_new.id == first.id
        assert len(versions_after_retry) == len(versions_after_first)
        assert retry_old.optimized_title == first.optimized_title

    async def test_first_approval_wins_for_the_same_source_token(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            first = await pipeline.preview(row.id, requested_by_user_id=None)
            second = await pipeline.preview(row.id, requested_by_user_id=None)
            await db_session.refresh(row)
            await pipeline.approve(
                row.id,
                version_id=first.candidate_version_id,
                expected_updated_at=row.updated_at,
            )
            # Production approve bumps `updated_at` in its own transaction.
            # This shared-transaction harness cannot observe that (Postgres
            # `now()` is frozen), so advance the token the way M2A tests do.
            await _advance_product_updated_at(db_session, row)
            with pytest.raises(ConflictError) as exc_info:
                await pipeline.approve(
                    row.id,
                    version_id=second.candidate_version_id,
                    expected_updated_at=row.updated_at,
                )
            versions = await ProductVersionRepository(db_session).list_for_product(row.id)
        finally:
            clear_context()

        assert exc_info.value.details.get("reason") == "stale_preview"
        by_id = {v.id: v for v in versions}
        assert by_id[first.candidate_version_id].active is True
        assert by_id[second.candidate_version_id].active is False

    async def test_stale_expected_updated_at_on_inactive_candidate(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            with pytest.raises(ConflictError) as exc_info:
                await pipeline.approve(
                    row.id,
                    version_id=preview.candidate_version_id,
                    expected_updated_at=datetime(2000, 1, 1, tzinfo=UTC),
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "draft_version_stale"

    async def test_merchant_edit_makes_inactive_candidate_stale(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            await db_session.refresh(row)
            await db_session.execute(
                update(Product)
                .where(Product.id == row.id)
                .values(
                    title="Merchant edited title",
                    updated_at=row.updated_at + timedelta(seconds=5),
                )
            )
            await db_session.flush()
            await db_session.refresh(row)
            with pytest.raises(ConflictError) as exc_info:
                await pipeline.approve(
                    row.id,
                    version_id=preview.candidate_version_id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "stale_preview"

    async def test_missing_expected_updated_at_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            with pytest.raises(ValidationError):
                await pipeline.approve(
                    row.id,
                    version_id=preview.candidate_version_id,
                    expected_updated_at=None,
                )
        finally:
            clear_context()

    async def test_original_snapshot_is_not_approvable(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            await pipeline.preview(row.id, requested_by_user_id=None)
            versions = await ProductVersionRepository(db_session).list_for_product(row.id)
            original = next(v for v in versions if v.source is ProductVersionSource.ORIGINAL)
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.approve(
                    row.id,
                    version_id=original.id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "original_not_approvable"

    async def test_legacy_optimize_version_is_not_a_pipeline_candidate(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await _import_product(client, monkeypatch)
        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        version_id = uuid.UUID(response.json()["version"]["id"])
        row = await _bind_tenant(db_session, product["id"])
        try:
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await ProductPipelineService(db_session).approve(
                    row.id,
                    version_id=version_id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "not_a_pipeline_candidate"

    async def test_oversized_title_is_not_truncated(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            version = await ProductVersionRepository(db_session).get_by_id_for_product(
                product_id=row.id, version_id=preview.candidate_version_id
            )
            assert version is not None
            oversized = "a" * (PIPELINE_APPROVAL_TITLE_MAX + 1)
            content = dict(version.content)
            content["title"] = oversized
            version.content = content
            flag_modified(version, "content")
            await db_session.flush()
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.approve(
                    row.id,
                    version_id=preview.candidate_version_id,
                    expected_updated_at=row.updated_at,
                )
            reloaded = await ProductVersionRepository(db_session).get_by_id_for_product(
                product_id=row.id, version_id=preview.candidate_version_id
            )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "candidate_content_invalid"
        assert reloaded is not None
        assert reloaded.active is False
        assert reloaded.content["title"] == oversized

    async def test_blank_title_is_invalid(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            version = await ProductVersionRepository(db_session).get_by_id_for_product(
                product_id=row.id, version_id=preview.candidate_version_id
            )
            assert version is not None
            content = dict(version.content)
            content["title"] = "   "
            version.content = content
            flag_modified(version, "content")
            await db_session.flush()
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.approve(
                    row.id,
                    version_id=preview.candidate_version_id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "candidate_content_invalid"

    async def test_foreign_tenant_is_indistinguishable_from_missing(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            await db_session.refresh(row)
            token = row.updated_at
            version_id = preview.candidate_version_id
            product_id = row.id
        finally:
            clear_context()

        set_tenant_id(uuid.uuid4())
        try:
            with pytest.raises(NotFoundError):
                await ProductPipelineService(db_session).approve(
                    product_id,
                    version_id=version_id,
                    expected_updated_at=token,
                )
        finally:
            clear_context()

    async def test_wrong_product_version_is_not_found(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, first = await _import_product(client, monkeypatch)
        patch_aliexpress(monkeypatch, id_echoing_handler)
        second = (
            await client.post(
                "/api/v1/products/import",
                json={"externalId": "3000000000001"},
                headers=headers,
            )
        ).json()
        assert second["id"] != first["id"]
        row = await _bind_tenant(db_session, first["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            await db_session.refresh(row)
            with pytest.raises(NotFoundError):
                await pipeline.approve(
                    uuid.UUID(second["id"]),
                    version_id=preview.candidate_version_id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()


class TestPublish:
    async def test_inactive_candidate_cannot_publish(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            preview = await pipeline.preview(row.id, requested_by_user_id=None)
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=preview.candidate_version_id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "candidate_not_approved"

    async def test_legacy_optimize_cannot_pipeline_publish(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        headers, product = await _import_product(client, monkeypatch)
        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        version_id = uuid.UUID(response.json()["version"]["id"])
        row = await _bind_tenant(db_session, product["id"])
        try:
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await ProductPipelineService(db_session).publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=version_id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "not_a_pipeline_candidate"

    async def test_synthetic_candidate_is_blocked(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            candidate = await _insert_pipeline_candidate(
                db_session, row, is_synthetic=True, ai_provider="test"
            )
            await db_session.refresh(row)
            await pipeline.approve(
                row.id, version_id=candidate.id, expected_updated_at=row.updated_at
            )
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=candidate.id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "synthetic_publish_blocked"
        assert candidate.active is True

    async def test_stub_provider_is_blocked_even_when_not_synthetic(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            candidate = await _insert_pipeline_candidate(
                db_session, row, is_synthetic=False, ai_provider="stub"
            )
            await db_session.refresh(row)
            await pipeline.approve(
                row.id, version_id=candidate.id, expected_updated_at=row.updated_at
            )
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=candidate.id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "synthetic_publish_blocked"

    async def test_missing_provider_is_unverified(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            candidate = await _insert_pipeline_candidate(
                db_session, row, is_synthetic=False, ai_provider=None
            )
            await db_session.refresh(row)
            await pipeline.approve(
                row.id, version_id=candidate.id, expected_updated_at=row.updated_at
            )
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=candidate.id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "ai_provenance_unverified"

    async def test_empty_provider_is_unverified(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            candidate = await _insert_pipeline_candidate(
                db_session, row, is_synthetic=False, ai_provider="   "
            )
            await db_session.refresh(row)
            await pipeline.approve(
                row.id, version_id=candidate.id, expected_updated_at=row.updated_at
            )
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=candidate.id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "ai_provenance_unverified"

    async def test_title_256_is_not_publishable(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            candidate = await _insert_pipeline_candidate(
                db_session,
                row,
                title="a" * (PIPELINE_PUBLISH_TITLE_MAX + 1),
                is_synthetic=False,
            )
            await db_session.refresh(row)
            await pipeline.approve(
                row.id, version_id=candidate.id, expected_updated_at=row.updated_at
            )
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=candidate.id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "candidate_title_not_publishable"

    async def test_oversized_sanitized_body_is_not_truncated(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        oversized = "a" * (DESCRIPTION_MAX_LENGTH + 1)
        try:
            pipeline = ProductPipelineService(db_session)
            candidate = await _insert_pipeline_candidate(
                db_session, row, description=oversized, is_synthetic=False
            )
            await db_session.refresh(row)
            await pipeline.approve(
                row.id, version_id=candidate.id, expected_updated_at=row.updated_at
            )
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=candidate.id,
                    expected_updated_at=row.updated_at,
                )
            reloaded = await ProductVersionRepository(db_session).get_by_id_for_product(
                product_id=row.id, version_id=candidate.id
            )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "candidate_description_too_long"
        assert reloaded is not None
        assert reloaded.content["description"] == oversized
        assert reloaded.active is True

    async def test_malformed_pipeline_metadata_is_rejected(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_must_not_run: None,
    ) -> None:
        _headers, product = await _import_product(client, monkeypatch)
        row = await _bind_tenant(db_session, product["id"])
        try:
            pipeline = ProductPipelineService(db_session)
            candidate = await _insert_pipeline_candidate(db_session, row, is_synthetic=False)
            await db_session.refresh(row)
            await pipeline.approve(
                row.id, version_id=candidate.id, expected_updated_at=row.updated_at
            )
            content = dict(candidate.content)
            content["pipelineCandidateVersion"] = True
            candidate.content = content
            flag_modified(candidate, "content")
            await db_session.flush()
            await db_session.refresh(row)
            with pytest.raises(ValidationError) as exc_info:
                await pipeline.publish(
                    row.id,
                    store_id=uuid.uuid4(),
                    version_id=candidate.id,
                    expected_updated_at=row.updated_at,
                )
        finally:
            clear_context()
        assert exc_info.value.details.get("reason") == "not_a_pipeline_candidate"
