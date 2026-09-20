"""HTTP contract for Phase 9 Stage 8 pipeline API.

Thin adapter over ProductPipelineService. StubProvider / fixture providers
only — not a live-model claim.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.context import set_tenant_id
from app.core.encryption import encrypt
from app.core.tokens import create_access_token
from app.integrations.aliexpress import service as service_module
from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.exceptions import ShopifyTimeoutError
from app.models.ai_prompt import PromptExecution
from app.models.integration import IntegrationStatus
from app.models.product import Product, ProductImage, ProductVersion, ProductVersionSource
from app.models.shopify import ShopifyConnection
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.user import User
from app.repositories.product import ProductVersionRepository
from app.schemas.product import DESCRIPTION_MAX_LENGTH
from app.services.image_analysis import ImageAnalysisReport, ImageAnalysisService
from app.services.product_pipeline import PIPELINE_PUBLISH_TITLE_MAX
from tests.integration.shopify_publish_live import CountingPublishShopify
from tests.integration.test_product_optimization import import_a_product
from tests.integration.test_product_pipeline import (
    _advance_product_updated_at,
    _insert_legacy_ai_version,
)
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

PIPELINE_PATHS = (
    "/api/v1/products/{product_id}/pipeline/preview",
    "/api/v1/products/{product_id}/pipeline/versions/{version_id}/preview",
    "/api/v1/products/{product_id}/pipeline/versions/{version_id}/approve",
    "/api/v1/products/{product_id}/pipeline/versions/{version_id}/publish",
)


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


@pytest.fixture
def shopify_wire(monkeypatch: pytest.MonkeyPatch) -> CountingPublishShopify:
    wire = CountingPublishShopify()
    monkeypatch.setattr(
        shopify_service_module,
        "ShopifyClient",
        lambda **kwargs: wire.client(**kwargs),
    )
    return wire


def _error_reason(body: dict[str, Any]) -> str | None:
    for detail in body.get("details") or []:
        if detail.get("type") == "reason":
            message = detail.get("message")
            return message if isinstance(message, str) else None
    return None


def _role_headers(
    tenant_id: uuid.UUID, role: str, *, user_id: uuid.UUID | None = None
) -> dict[str, str]:
    token = create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        roles=(role,),
    )
    return {"Authorization": f"Bearer {token.token}"}


async def _tenant_user(db_session: AsyncSession, tenant_id: uuid.UUID) -> User:
    return (await db_session.execute(select(User).where(User.tenant_id == tenant_id))).scalar_one()


async def _product_row(db_session: AsyncSession, product_id: str) -> Product:
    return (
        await db_session.execute(select(Product).where(Product.id == uuid.UUID(product_id)))
    ).scalar_one()


async def _execution_count(db_session: AsyncSession, tenant_id: uuid.UUID) -> int:
    result = await db_session.execute(
        select(func.count())
        .select_from(PromptExecution)
        .where(PromptExecution.tenant_id == tenant_id)
    )
    return int(result.scalar_one())


async def _version_count(db_session: AsyncSession, product_id: uuid.UUID) -> int:
    result = await db_session.execute(
        select(func.count())
        .select_from(ProductVersion)
        .where(ProductVersion.product_id == product_id)
    )
    return int(result.scalar_one())


async def _seed_store(db_session: AsyncSession, product: Product) -> Store:
    suffix = uuid.uuid4().hex[:8]
    country = product.import_ship_to_country or "US"
    store = Store(
        tenant_id=product.tenant_id,
        name=f"Pipeline store {suffix}",
        slug=f"pipe-{suffix}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="USD",
        settings={"countryCode": country},
    )
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        ShopifyConnection(
            tenant_id=product.tenant_id,
            store_id=store.id,
            shop_domain=f"pipe-{suffix}.myshopify.com",
            encrypted_access_token=encrypt(f"shpat_{suffix}"),
            scopes="write_products",
            status=IntegrationStatus.CONNECTED,
        )
    )
    await db_session.flush()
    return store


async def _make_publishable(db_session: AsyncSession, version_id: uuid.UUID) -> ProductVersion:
    """Fixture provenance — not a live model. Required for overlay publish."""
    version = (
        await db_session.execute(select(ProductVersion).where(ProductVersion.id == version_id))
    ).scalar_one()
    content = dict(version.content or {})
    content["isSynthetic"] = False
    version.content = content
    flag_modified(version, "content")
    version.ai_provider = "test"
    await db_session.flush()
    return version


def _preview_url(product_id: str) -> str:
    return f"/api/v1/products/{product_id}/pipeline/preview"


def _existing_preview_url(product_id: str, version_id: str) -> str:
    return f"/api/v1/products/{product_id}/pipeline/versions/{version_id}/preview"


def _approve_url(product_id: str, version_id: str) -> str:
    return f"/api/v1/products/{product_id}/pipeline/versions/{version_id}/approve"


def _publish_url(product_id: str, version_id: str) -> str:
    return f"/api/v1/products/{product_id}/pipeline/versions/{version_id}/publish"


class TestOpenApiAndSchema:
    async def test_four_pipeline_paths_are_documented(self, client: AsyncClient) -> None:
        spec = (await client.get("/openapi.json")).json()
        paths = spec["paths"]
        for template in PIPELINE_PATHS:
            assert template in paths, template
        assert "/api/v1/products/pipeline" not in paths
        post = paths["/api/v1/products/{product_id}/pipeline/preview"]["post"]
        assert "201" in post["responses"]
        get = paths["/api/v1/products/{product_id}/pipeline/versions/{version_id}/preview"]["get"]
        assert "200" in get["responses"]
        schemas = spec["components"]["schemas"]
        for name in (
            "PipelinePreviewRequest",
            "PipelinePreviewResponse",
            "PipelineApproveRequest",
            "PipelinePublishRequest",
            "PipelineListingViewRead",
            "PipelineImageAnalysisEvidenceRead",
            "PipelineImageAnalysisItemRead",
        ):
            assert name in schemas, name

    async def test_preview_accepts_camel_case_and_empty_body(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        empty = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        assert empty.status_code == 201, empty.text
        body = empty.json()
        assert "candidateVersionId" in body
        assert "approvalExpectedUpdatedAt" in body
        assert "imageAnalysis" in body
        assert "pipelineBlockers" in body
        assert "isSynthetic" in body

    async def test_approve_and_publish_reject_missing_null_malformed_and_extra(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        approve = _approve_url(product["id"], version_id)
        publish = _publish_url(product["id"], version_id)

        assert (await client.post(approve, json={}, headers=headers)).status_code == 422
        assert (
            await client.post(approve, json={"expectedUpdatedAt": None}, headers=headers)
        ).status_code == 422
        assert (
            await client.post(approve, json={"expectedUpdatedAt": "not-a-time"}, headers=headers)
        ).status_code == 422
        assert (
            await client.post(
                approve,
                json={"expectedUpdatedAt": preview.json()["approvalExpectedUpdatedAt"], "extra": 1},
                headers=headers,
            )
        ).status_code == 422

        assert (
            await client.post(publish, json={"storeId": str(uuid.uuid4())}, headers=headers)
        ).status_code == 422
        assert (
            await client.post(
                publish,
                json={"storeId": str(uuid.uuid4()), "expectedUpdatedAt": None},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.post(
                publish,
                json={"storeId": str(uuid.uuid4()), "expectedUpdatedAt": "nope"},
                headers=headers,
            )
        ).status_code == 422
        assert (
            await client.post(
                publish,
                json={
                    "storeId": str(uuid.uuid4()),
                    "expectedUpdatedAt": preview.json()["approvalExpectedUpdatedAt"],
                    "extra": 1,
                },
                headers=headers,
            )
        ).status_code == 422
        extra_preview = await client.post(
            _preview_url(product["id"]),
            json={"tone": "professional", "extra": 1},
            headers=headers,
        )
        assert extra_preview.status_code == 422

    async def test_malformed_uuid_is_422(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, _product = await import_a_product(client, monkeypatch)
        response = await client.post(
            "/api/v1/products/not-a-uuid/pipeline/preview", json={}, headers=headers
        )
        assert response.status_code == 422


class TestAuth:
    async def test_unauthenticated_is_401_on_all_four(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        pid = product["id"]
        calls = (
            ("POST", _preview_url(pid), {}),
            ("GET", _existing_preview_url(pid, version_id), None),
            (
                "POST",
                _approve_url(pid, version_id),
                {"expectedUpdatedAt": preview.json()["approvalExpectedUpdatedAt"]},
            ),
            (
                "POST",
                _publish_url(pid, version_id),
                {
                    "storeId": str(uuid.uuid4()),
                    "expectedUpdatedAt": preview.json()["approvalExpectedUpdatedAt"],
                },
            ),
        )
        for method, url, payload in calls:
            if method == "GET":
                response = await client.get(url)
            else:
                response = await client.post(url, json=payload)
            assert response.status_code == 401, response.text

    async def test_viewer_and_member_are_403(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        row = await _product_row(db_session, product["id"])
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        pid = product["id"]
        for role in ("viewer", "member"):
            role_headers = _role_headers(row.tenant_id, role)
            assert (
                await client.post(_preview_url(pid), json={}, headers=role_headers)
            ).status_code == 403
            assert (
                await client.get(_existing_preview_url(pid, version_id), headers=role_headers)
            ).status_code == 403
            assert (
                await client.post(
                    _approve_url(pid, version_id),
                    json={"expectedUpdatedAt": t0},
                    headers=role_headers,
                )
            ).status_code == 403
            assert (
                await client.post(
                    _publish_url(pid, version_id),
                    json={"storeId": str(uuid.uuid4()), "expectedUpdatedAt": t0},
                    headers=role_headers,
                )
            ).status_code == 403

    async def test_admin_and_owner_can_preview(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        owner = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        assert owner.status_code == 201, owner.text
        version_id = owner.json()["candidateVersionId"]
        t0 = owner.json()["approvalExpectedUpdatedAt"]
        owner_get = await client.get(
            _existing_preview_url(product["id"], version_id), headers=headers
        )
        assert owner_get.status_code == 200, owner_get.text
        row = await _product_row(db_session, product["id"])
        user = await _tenant_user(db_session, row.tenant_id)
        admin_headers = _role_headers(row.tenant_id, "admin", user_id=user.id)
        admin = await client.post(_preview_url(product["id"]), json={}, headers=admin_headers)
        assert admin.status_code == 201, admin.text
        admin_get = await client.get(
            _existing_preview_url(product["id"], admin.json()["candidateVersionId"]),
            headers=admin_headers,
        )
        assert admin_get.status_code == 200, admin_get.text
        owner_approve = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert owner_approve.status_code == 200, owner_approve.text
        admin_publish = await client.post(
            _publish_url(product["id"], version_id),
            json={
                "storeId": str(uuid.uuid4()),
                "expectedUpdatedAt": owner_approve.json()["updatedAt"],
            },
            headers=admin_headers,
        )
        assert admin_publish.status_code in {404, 422}


class TestTenantIsolation:
    async def test_foreign_product_is_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _headers, product = await import_a_product(client, monkeypatch)
        other = await register(client)
        response = await client.post(
            _preview_url(product["id"]), json={}, headers=auth_header(other)
        )
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "not_found"

    async def test_wrong_product_version_and_foreign_version_are_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers_a, product_a = await import_a_product(client, monkeypatch)
        headers_b, product_b = await import_a_product(client, monkeypatch)
        preview_a = await client.post(_preview_url(product_a["id"]), json={}, headers=headers_a)
        version_a = preview_a.json()["candidateVersionId"]
        wrong_product = await client.get(
            _existing_preview_url(product_b["id"], version_a), headers=headers_b
        )
        assert wrong_product.status_code == 404, wrong_product.text
        foreign = await client.get(
            _existing_preview_url(product_a["id"], version_a), headers=headers_b
        )
        assert foreign.status_code == 404, foreign.text
        missing = await client.get(
            _existing_preview_url(product_a["id"], str(uuid.uuid4())), headers=headers_a
        )
        assert missing.status_code == 404
        assert foreign.json()["code"] == missing.json()["code"] == "not_found"

    async def test_foreign_store_is_404_on_preview_get_and_publish(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers_a, product_a = await import_a_product(client, monkeypatch)
        _headers_b, product_b = await import_a_product(client, monkeypatch)
        row_b = await _product_row(db_session, product_b["id"])
        set_tenant_id(row_b.tenant_id)
        store_b = await _seed_store(db_session, row_b)
        preview = await client.post(_preview_url(product_a["id"]), json={}, headers=headers_a)
        version_id = preview.json()["candidateVersionId"]
        t0 = preview.json()["approvalExpectedUpdatedAt"]

        post_preview = await client.post(
            _preview_url(product_a["id"]),
            json={"storeId": str(store_b.id)},
            headers=headers_a,
        )
        assert post_preview.status_code == 404, post_preview.text

        get_preview = await client.get(
            _existing_preview_url(product_a["id"], version_id),
            params={"storeId": str(store_b.id)},
            headers=headers_a,
        )
        assert get_preview.status_code == 404, get_preview.text

        await _make_publishable(db_session, uuid.UUID(version_id))
        approved = await client.post(
            _approve_url(product_a["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers_a,
        )
        assert approved.status_code == 200, approved.text
        publish = await client.post(
            _publish_url(product_a["id"], version_id),
            json={"storeId": str(store_b.id), "expectedUpdatedAt": approved.json()["updatedAt"]},
            headers=headers_a,
        )
        assert publish.status_code == 404, publish.text
        assert publish.json()["code"] == "not_found"


class TestPostPreview:
    async def test_creates_inactive_candidate_without_activating_or_publishing(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        merchant_title = product["title"]
        before_versions = await _version_count(db_session, uuid.UUID(product["id"]))
        response = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["candidateActive"] is False
        assert body["candidateVersionId"]
        assert body["candidateVersionNumber"] >= 2
        assert body["sourceUpdatedAt"]
        assert body["approvalExpectedUpdatedAt"]
        assert body["channelReadiness"] is None
        assert body["publishable"] is False
        assert body["isSynthetic"] is True
        assert body["provider"] == "stub"
        assert any(item["code"] == "synthetic_publish_blocked" for item in body["pipelineBlockers"])
        after = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()
        assert after["optimizedTitle"] is None
        assert after["title"] == merchant_title
        assert await _version_count(db_session, uuid.UUID(product["id"])) == before_versions + 2

    async def test_store_projects_channel_readiness(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        row = await _product_row(db_session, product["id"])
        set_tenant_id(row.tenant_id)
        store = await _seed_store(db_session, row)
        response = await client.post(
            _preview_url(product["id"]), json={"storeId": str(store.id)}, headers=headers
        )
        assert response.status_code == 201, response.text
        readiness = response.json()["channelReadiness"]
        assert readiness is not None
        assert readiness["channel"] == "shopify"
        assert readiness["storeId"] == str(store.id)
        assert "canPublish" in readiness
        assert "blockers" in readiness


class TestGetPreview:
    async def test_returns_exact_candidate_without_regenerating(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        created = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = created.json()["candidateVersionId"]
        pid = uuid.UUID(product["id"])
        row = await _product_row(db_session, product["id"])
        versions_before = await _version_count(db_session, pid)
        executions_before = await _execution_count(db_session, row.tenant_id)
        fetched = await client.get(
            _existing_preview_url(product["id"], version_id), headers=headers
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["candidateVersionId"] == version_id
        assert await _version_count(db_session, pid) == versions_before
        assert await _execution_count(db_session, row.tenant_id) == executions_before

    async def test_rejects_original_legacy_and_corrupt_markers(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        await client.post(_preview_url(product["id"]), json={}, headers=headers)
        row = await _product_row(db_session, product["id"])
        set_tenant_id(row.tenant_id)
        versions = await ProductVersionRepository(db_session).list_for_product(row.id)
        original = next(v for v in versions if v.source is ProductVersionSource.ORIGINAL)
        original_resp = await client.get(
            _existing_preview_url(product["id"], str(original.id)), headers=headers
        )
        assert original_resp.status_code == 422, original_resp.text
        assert _error_reason(original_resp.json()) == "not_a_pipeline_candidate"

        set_tenant_id(row.tenant_id)
        legacy = await _insert_legacy_ai_version(db_session, row)
        await db_session.flush()
        legacy_resp = await client.get(
            _existing_preview_url(product["id"], str(legacy.id)), headers=headers
        )
        assert legacy_resp.status_code == 422
        assert _error_reason(legacy_resp.json()) == "not_a_pipeline_candidate"

        pipeline = next(v for v in versions if v.source is ProductVersionSource.AI_GENERATED)
        content = dict(pipeline.content or {})
        content["pipelineCandidateVersion"] = True
        pipeline.content = content
        flag_modified(pipeline, "content")
        await db_session.flush()
        corrupt = await client.get(
            _existing_preview_url(product["id"], str(pipeline.id)), headers=headers
        )
        assert corrupt.status_code == 422
        assert _error_reason(corrupt.json()) == "not_a_pipeline_candidate"

    async def test_null_image_analysis_projects_as_null(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        created = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = created.json()["candidateVersionId"]
        pid = uuid.UUID(product["id"])
        row = await _product_row(db_session, product["id"])
        images = (
            (await db_session.execute(select(ProductImage).where(ProductImage.product_id == pid)))
            .scalars()
            .all()
        )
        assert images
        for image in images:
            image.analysis = None
        await db_session.flush()

        fetch_called = {"n": 0}

        async def _forbid_analyse(
            self: ImageAnalysisService,
            product_id: uuid.UUID,
            *,
            executed_by_user_id: uuid.UUID | None = None,
        ) -> ImageAnalysisReport:
            fetch_called["n"] += 1
            raise AssertionError("GET preview must not analyse images")

        monkeypatch.setattr(ImageAnalysisService, "analyse_product_images", _forbid_analyse)
        executions_before = await _execution_count(db_session, row.tenant_id)
        response = await client.get(
            _existing_preview_url(product["id"], version_id), headers=headers
        )
        assert response.status_code == 200, response.text
        items = response.json()["imageAnalysis"]["images"]
        assert items
        for item in items:
            assert item["status"] == "unknown"
            assert item["errorCode"] is None
            assert item["analysis"] is None
        assert fetch_called["n"] == 0
        assert await _execution_count(db_session, row.tenant_id) == executions_before
        reloaded = (
            (await db_session.execute(select(ProductImage).where(ProductImage.product_id == pid)))
            .scalars()
            .all()
        )
        assert all(image.analysis is None for image in reloaded)

    async def test_non_empty_and_failure_evidence_are_typed(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        created = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = created.json()["candidateVersionId"]
        pid = uuid.UUID(product["id"])
        images = (
            (await db_session.execute(select(ProductImage).where(ProductImage.product_id == pid)))
            .scalars()
            .all()
        )
        images[0].analysis = {
            "imageAnalysisVersion": 1,
            "sourceUrl": "https://cdn.example/ok.jpg",
            "contentSha256": "abc",
            "byteLength": 12,
            "decodedWidth": 10,
            "decodedHeight": 10,
            "decodedFormat": "jpeg",
            "status": "succeeded",
            "errorCode": None,
            "checks": {
                "blur": {
                    "applicable": True,
                    "blurScore": 12,
                    "isBlurry": False,
                    "threshold": 100,
                    "workingSize": 256,
                },
                "duplicates": {
                    "applicable": True,
                    "contentSha256": "abc",
                    "duplicateOfImageIds": [],
                },
                "watermark": {
                    "applicable": False,
                    "reason": "genericWatermarkDetectionNotImplemented",
                },
            },
            "captionProposal": "cap",
            "altTextProposal": "alt",
            "isSynthetic": True,
            "provider": "stub",
            "model": "stub-1",
            "promptName": "image_analyzer",
            "promptVersion": 1,
        }
        if len(images) > 1:
            images[1].analysis = {
                "imageAnalysisVersion": 1,
                "sourceUrl": "https://cdn.example/fail.jpg",
                "contentSha256": None,
                "byteLength": None,
                "decodedWidth": None,
                "decodedHeight": None,
                "decodedFormat": None,
                "status": "fetchFailed",
                "errorCode": "ImageFetchTimeout",
                "checks": None,
                "captionProposal": None,
                "altTextProposal": None,
                "isSynthetic": None,
                "provider": None,
                "model": None,
                "promptName": None,
                "promptVersion": None,
            }
        await db_session.flush()
        response = await client.get(
            _existing_preview_url(product["id"], version_id), headers=headers
        )
        assert response.status_code == 200, response.text
        first = response.json()["imageAnalysis"]["images"][0]["analysis"]
        assert first["checks"]["blur"]["blurScore"] == 12
        assert first["checks"]["duplicates"]["contentSha256"] == "abc"
        assert first["checks"]["watermark"]["reason"] == "genericWatermarkDetectionNotImplemented"
        if len(images) > 1:
            failed = response.json()["imageAnalysis"]["images"][1]["analysis"]
            assert failed["contentSha256"] is None
            assert failed["checks"] is None
            assert failed["errorCode"] == "ImageFetchTimeout"

    async def test_malformed_non_empty_evidence_is_internal_error(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        created = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = created.json()["candidateVersionId"]
        pid = uuid.UUID(product["id"])
        image = (
            (await db_session.execute(select(ProductImage).where(ProductImage.product_id == pid)))
            .scalars()
            .first()
        )
        assert image is not None
        image.analysis = {"status": "succeeded", "notARealField": True}
        await db_session.flush()
        response = await client.get(
            _existing_preview_url(product["id"], version_id), headers=headers
        )
        assert response.status_code == 500, response.text
        assert response.json()["code"] == "internal_error"


class TestApprove:
    async def test_approves_with_t0_and_bumps_updated_at_token(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        merchant_title = product["title"]
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        version_id = preview.json()["candidateVersionId"]
        approved = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["optimizedTitle"] == preview.json()["proposal"]["title"]
        assert body["title"] == merchant_title
        row = await _product_row(db_session, product["id"])
        await _advance_product_updated_at(db_session, row)
        t1 = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()[
            "updatedAt"
        ]
        assert t1 != t0
        follow = await client.get(_existing_preview_url(product["id"], version_id), headers=headers)
        assert follow.json()["candidateActive"] is True

    async def test_stale_token_and_stale_preview_and_active_retry(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        stale = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": "2000-01-01T00:00:00+00:00"},
            headers=headers,
        )
        assert stale.status_code == 409, stale.text
        assert _error_reason(stale.json()) == "draft_version_stale"

        row = await _product_row(db_session, product["id"])
        await _advance_product_updated_at(db_session, row)
        edited = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": row.updated_at.isoformat()},
            headers=headers,
        )
        assert edited.status_code == 409, edited.text
        assert _error_reason(edited.json()) == "stale_preview"

        headers2, product2 = await import_a_product(client, monkeypatch)
        preview2 = await client.post(_preview_url(product2["id"]), json={}, headers=headers2)
        t0b = preview2.json()["approvalExpectedUpdatedAt"]
        vid = preview2.json()["candidateVersionId"]
        first = await client.post(
            _approve_url(product2["id"], vid), json={"expectedUpdatedAt": t0b}, headers=headers2
        )
        assert first.status_code == 200, first.text
        retry = await client.post(
            _approve_url(product2["id"], vid), json={"expectedUpdatedAt": t0b}, headers=headers2
        )
        assert retry.status_code == 200, retry.text
        row2 = await _product_row(db_session, product2["id"])
        await _advance_product_updated_at(db_session, row2)
        current = (await client.get(f"/api/v1/products/{product2['id']}", headers=headers2)).json()[
            "updatedAt"
        ]
        retry_after = await client.post(
            _approve_url(product2["id"], vid), json={"expectedUpdatedAt": t0b}, headers=headers2
        )
        assert retry_after.status_code == 200, retry_after.text
        assert retry_after.json()["updatedAt"] == current

    async def test_sibling_and_legacy_activate(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        first = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        second = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        t0 = first.json()["approvalExpectedUpdatedAt"]
        ok = await client.post(
            _approve_url(product["id"], first.json()["candidateVersionId"]),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        row = await _product_row(db_session, product["id"])
        await _advance_product_updated_at(db_session, row)
        sibling = await client.post(
            _approve_url(product["id"], second.json()["candidateVersionId"]),
            json={"expectedUpdatedAt": row.updated_at.isoformat()},
            headers=headers,
        )
        assert sibling.status_code == 409, sibling.text
        assert _error_reason(sibling.json()) == "stale_preview"

        headers3, product3 = await import_a_product(client, monkeypatch)
        preview3 = await client.post(_preview_url(product3["id"]), json={}, headers=headers3)
        activate = await client.post(
            f"/api/v1/products/{product3['id']}/versions/{preview3.json()['candidateVersionId']}/activate",
            headers=headers3,
        )
        assert activate.status_code == 422, activate.text
        assert _error_reason(activate.json()) == "pipeline_candidate_requires_approval"


class TestPublishFailures:
    async def test_inactive_synthetic_stub_and_missing_provider(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        row = await _product_row(db_session, product["id"])
        set_tenant_id(row.tenant_id)
        store = await _seed_store(db_session, row)

        inactive = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": t0},
            headers=headers,
        )
        assert inactive.status_code == 422, inactive.text
        assert _error_reason(inactive.json()) == "candidate_not_approved"

        approved = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        synthetic = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": approved.json()["updatedAt"]},
            headers=headers,
        )
        assert synthetic.status_code == 422, synthetic.text
        assert _error_reason(synthetic.json()) == "synthetic_publish_blocked"

        version = await _make_publishable(db_session, uuid.UUID(version_id))
        version.ai_provider = "stub"
        await db_session.flush()
        stub = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": approved.json()["updatedAt"]},
            headers=headers,
        )
        assert stub.status_code == 422
        assert _error_reason(stub.json()) == "synthetic_publish_blocked"

        version.ai_provider = None
        content = dict(version.content or {})
        content["isSynthetic"] = False
        version.content = content
        flag_modified(version, "content")
        await db_session.flush()
        missing = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": approved.json()["updatedAt"]},
            headers=headers,
        )
        assert missing.status_code == 422
        assert _error_reason(missing.json()) == "ai_provenance_unverified"

    async def test_title_and_description_bounds_are_422(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        row = await _product_row(db_session, product["id"])
        set_tenant_id(row.tenant_id)
        store = await _seed_store(db_session, row)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        version = await _make_publishable(db_session, uuid.UUID(version_id))
        content = dict(version.content or {})
        content["title"] = "a" * (PIPELINE_PUBLISH_TITLE_MAX + 1)
        version.content = content
        flag_modified(version, "content")
        await db_session.flush()
        approved = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        too_long_title = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": approved.json()["updatedAt"]},
            headers=headers,
        )
        assert too_long_title.status_code == 422, too_long_title.text
        assert _error_reason(too_long_title.json()) == "candidate_title_not_publishable"

        headers2, product2 = await import_a_product(client, monkeypatch)
        row2 = await _product_row(db_session, product2["id"])
        set_tenant_id(row2.tenant_id)
        store2 = await _seed_store(db_session, row2)
        preview2 = await client.post(_preview_url(product2["id"]), json={}, headers=headers2)
        vid2 = preview2.json()["candidateVersionId"]
        t0b = preview2.json()["approvalExpectedUpdatedAt"]
        version2 = await _make_publishable(db_session, uuid.UUID(vid2))
        content2 = dict(version2.content or {})
        content2["description"] = "a" * (DESCRIPTION_MAX_LENGTH + 1)
        version2.content = content2
        flag_modified(version2, "content")
        await db_session.flush()
        approved2 = await client.post(
            _approve_url(product2["id"], vid2),
            json={"expectedUpdatedAt": t0b},
            headers=headers2,
        )
        assert approved2.status_code == 200, approved2.text
        too_long_body = await client.post(
            _publish_url(product2["id"], vid2),
            json={"storeId": str(store2.id), "expectedUpdatedAt": approved2.json()["updatedAt"]},
            headers=headers2,
        )
        assert too_long_body.status_code == 422, too_long_body.text
        assert _error_reason(too_long_body.json()) == "candidate_description_too_long"


class TestPublishLifecycle:
    async def test_t0_then_t1_and_lost_response_retry(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_wire: CountingPublishShopify,
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        row = await _product_row(db_session, product["id"])
        set_tenant_id(row.tenant_id)
        store = await _seed_store(db_session, row)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        assert preview.status_code == 201, preview.text
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        version_id = preview.json()["candidateVersionId"]
        await _make_publishable(db_session, uuid.UUID(version_id))

        approved = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        row = await _product_row(db_session, product["id"])
        await _advance_product_updated_at(db_session, row)
        t1 = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()[
            "updatedAt"
        ]
        assert t1 != t0

        stale = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": t0},
            headers=headers,
        )
        assert stale.status_code == 409, stale.text
        assert _error_reason(stale.json()) == "draft_version_stale"
        assert shopify_wire.creates == []

        ok = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": t1},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["listingId"]
        assert body["externalProductId"]
        assert shopify_wire.creates
        payload = shopify_wire.creates[0]
        assert "AI SEO TITLE MUST NOT PUBLISH" not in str(payload)

    async def test_lost_approve_response_returns_t1_for_publish(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_wire: CountingPublishShopify,
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        row = await _product_row(db_session, product["id"])
        set_tenant_id(row.tenant_id)
        store = await _seed_store(db_session, row)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        version_id = preview.json()["candidateVersionId"]
        await _make_publishable(db_session, uuid.UUID(version_id))
        first = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        row = await _product_row(db_session, product["id"])
        await _advance_product_updated_at(db_session, row)
        retry = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert retry.status_code == 200, retry.text
        t1 = retry.json()["updatedAt"]
        published = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": t1},
            headers=headers,
        )
        assert published.status_code == 200, published.text
        assert shopify_wire.creates

    async def test_overlay_uses_ai_title_not_ai_seo(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_wire: CountingPublishShopify,
    ) -> None:
        captured: list[dict[str, Any]] = []
        original = shopify_wire._on_create

        async def _wrap(shop_domain: str, body: dict[str, Any]) -> dict[str, Any]:
            captured.append(body)
            return await original(shop_domain, body)

        shopify_wire._on_create = _wrap  # type: ignore[method-assign]
        headers, product = await import_a_product(client, monkeypatch)
        row = await _product_row(db_session, product["id"])
        row.seo_title = "Merchant SEO title"
        row.seo_description = "Merchant SEO description"
        row.tags = ["keep-me"]
        await db_session.flush()
        set_tenant_id(row.tenant_id)
        store = await _seed_store(db_session, row)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        version = await _make_publishable(db_session, uuid.UUID(version_id))
        content = dict(version.content or {})
        content["title"] = "Approved AI title"
        content["description"] = "<p>Keep</p><script>alert(1)</script>"
        content["seoTitle"] = "AI SEO TITLE MUST NOT PUBLISH"
        content["seoDescription"] = "AI SEO DESC MUST NOT PUBLISH"
        content["keywords"] = "ai,must,not,become,tags"
        version.content = content
        flag_modified(version, "content")
        await db_session.flush()
        approved = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        row = await _product_row(db_session, product["id"])
        await _advance_product_updated_at(db_session, row)
        t1 = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()[
            "updatedAt"
        ]
        published = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": t1},
            headers=headers,
        )
        assert published.status_code == 200, published.text
        assert captured
        body = captured[0]["product"]
        assert body["title"] == "Approved AI title"
        assert "<script>" not in body["body_html"]
        assert body.get("metafields_global_title_tag") == "Merchant SEO title"
        assert body.get("metafields_global_description_tag") == "Merchant SEO description"
        assert "keep-me" in body["tags"]
        assert "AI SEO TITLE MUST NOT PUBLISH" not in str(body)
        assert "ai,must,not,become,tags" not in body["tags"]
        live = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()
        assert live["title"] != "Approved AI title"
        assert live["seoTitle"] == "Merchant SEO title"

    async def test_shopify_failure_leaves_candidate_active(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        shopify_wire: CountingPublishShopify,
    ) -> None:
        shopify_wire.fail_next_create_with = ShopifyTimeoutError("boom")
        headers, product = await import_a_product(client, monkeypatch)
        row = await _product_row(db_session, product["id"])
        set_tenant_id(row.tenant_id)
        store = await _seed_store(db_session, row)
        preview = await client.post(_preview_url(product["id"]), json={}, headers=headers)
        version_id = preview.json()["candidateVersionId"]
        t0 = preview.json()["approvalExpectedUpdatedAt"]
        await _make_publishable(db_session, uuid.UUID(version_id))
        approved = await client.post(
            _approve_url(product["id"], version_id),
            json={"expectedUpdatedAt": t0},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        row = await _product_row(db_session, product["id"])
        await _advance_product_updated_at(db_session, row)
        t1 = (await client.get(f"/api/v1/products/{product['id']}", headers=headers)).json()[
            "updatedAt"
        ]
        failed = await client.post(
            _publish_url(product["id"], version_id),
            json={"storeId": str(store.id), "expectedUpdatedAt": t1},
            headers=headers,
        )
        assert failed.status_code == 503, failed.text
        follow = await client.get(_existing_preview_url(product["id"], version_id), headers=headers)
        assert follow.json()["candidateActive"] is True


class TestLegacyOptimizeUnchanged:
    async def test_optimize_still_auto_activates(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert response.status_code == 201, response.text
        assert response.json()["version"]["active"] is True
        assert "pipelineCandidateVersion" not in str(response.json()["version"])
