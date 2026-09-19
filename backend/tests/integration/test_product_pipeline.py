"""Integration tests for Stage 7 ProductPipelineService."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import clear_context, set_tenant_id
from app.core.exceptions import ValidationError
from app.integrations.aliexpress import service as service_module
from app.models.ai_prompt import PromptExecution
from app.models.product import Product, ProductVersionSource
from app.repositories.product import ProductVersionRepository
from app.services.image_analysis import ImageAnalysisReport, ImageAnalysisService
from app.services.product_pipeline import ProductPipelineService
from tests.integration.test_products import REAL_PRODUCT_ID, connected_tenant

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
