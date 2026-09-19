"""ImageAnalysisService against a real database. No public internet."""

from __future__ import annotations

import hashlib
import uuid
from io import BytesIO
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.image_fetch import ImageFetchNotGlobalAddress
from app.core.config import AIProviderName, settings
from app.core.context import set_tenant_id
from app.core.exceptions import NotFoundError
from app.models.ai_prompt import PromptExecution
from app.models.product import (
    Product,
    ProductAIStatus,
    ProductImage,
    ProductSource,
    ProductStatus,
    ProductVersion,
)
from app.models.tenant import Tenant
from app.repositories.product import ProductImageRepository
from app.services.image_analysis import ImageAnalysisService
from tests.integration.conftest import DATABASE_AVAILABLE
from tests.integration.test_products import auth_header, register

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_AVAILABLE,
        reason="PostgreSQL is not reachable; set POSTGRES_* to run integration tests.",
    ),
]


def _png(color: tuple[int, int, int] = (40, 40, 40)) -> bytes:
    image = Image.new("RGB", (32, 32), color)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


class _BytesFetcher:
    """Injected downloader: URL → bytes or an expected fetch error."""

    def __init__(self, bodies: dict[str, bytes] | bytes) -> None:
        self.bodies = bodies

    async def fetch(self, url: str) -> bytes:
        if isinstance(self.bodies, bytes):
            return self.bodies
        if url not in self.bodies:
            raise ImageFetchNotGlobalAddress()
        return self.bodies[url]


async def _register(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = await register(client)
    return auth_header(body), uuid.UUID(str(body["identity"]["tenant"]["id"]))


async def _seed_product(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str = "Analysed mug",
) -> Product:
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title=title,
        status=ProductStatus.DRAFT,
        seo_title="Keep SEO",
        seo_description="Keep SEO description",
        optimized_title="Keep optimized",
        ai_status=ProductAIStatus.OPTIMIZED,
    )
    db_session.add(product)
    await db_session.flush()
    return product


async def _add_image(
    db_session: AsyncSession,
    product: Product,
    *,
    url: str,
    position: int,
    alt_text: str | None = None,
    is_supplier: bool = True,
) -> ProductImage:
    image = ProductImage(
        tenant_id=product.tenant_id,
        product_id=product.id,
        url=url,
        position=position,
        alt_text=alt_text,
        is_supplier=is_supplier,
    )
    db_session.add(image)
    await db_session.flush()
    return image


class TestImageAnalysisIntegration:
    async def test_own_product_records_synthetic_stub_evidence(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _headers, tenant_id = await _register(client)
        product = await _seed_product(db_session, tenant_id=tenant_id)
        url = "https://cdn.example/a.png"
        image = await _add_image(db_session, product, url=url, position=0, alt_text="Merchant alt")
        await db_session.refresh(product)
        updated_before = product.updated_at
        body = _png()
        digest = hashlib.sha256(url.encode()).hexdigest()[:8]
        service = ImageAnalysisService(db_session, fetcher=_BytesFetcher(body))  # type: ignore[arg-type]

        report = await service.analyse_product_images(product.id)

        assert len(report.images) == 1
        assert report.images[0].status == "succeeded"
        assert image.analysis is not None
        assert image.analysis["status"] == "succeeded"
        assert image.analysis["isSynthetic"] is True
        assert image.analysis["provider"] == "stub"
        assert image.analysis["model"] == "stub-1"
        assert image.analysis["promptName"] == "image_analyzer"
        assert image.analysis["captionProposal"] == (
            f"[STUB-AI] synthetic caption (image {digest})."
        )
        assert image.analysis["altTextProposal"] == (
            f"[STUB-AI] synthetic alt text (image {digest})."
        )
        assert image.analysis["checks"]["watermark"] == {
            "applicable": False,
            "reason": "genericWatermarkDetectionNotImplemented",
        }
        assert image.alt_text == "Merchant alt"
        assert image.url == url
        assert image.position == 0
        assert image.is_supplier is True
        await db_session.refresh(product)
        assert product.updated_at == updated_before
        assert product.seo_title == "Keep SEO"
        assert product.seo_description == "Keep SEO description"
        assert product.optimized_title == "Keep optimized"
        assert product.ai_status == ProductAIStatus.OPTIMIZED
        versions = (
            await db_session.execute(
                select(func.count())
                .select_from(ProductVersion)
                .where(ProductVersion.product_id == product.id)
            )
        ).scalar_one()
        assert versions == 0
        executions = (
            (
                await db_session.execute(
                    select(PromptExecution.prompt_name).where(
                        PromptExecution.tenant_id == tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert executions == ["image_analyzer"]

    async def test_foreign_tenant_product_is_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _headers_a, tenant_a = await _register(client)
        _headers_b, tenant_b = await _register(client)
        product_b = await _seed_product(db_session, tenant_id=tenant_b)
        await _add_image(db_session, product_b, url="https://cdn.example/a.png", position=0)
        set_tenant_id(tenant_a)
        service = ImageAnalysisService(db_session, fetcher=_BytesFetcher(_png()))  # type: ignore[arg-type]

        with pytest.raises(NotFoundError):
            await service.analyse_product_images(product_b.id)

    async def test_merchant_image_survives_supplier_sync(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _headers, tenant_id = await _register(client)
        product = await _seed_product(db_session, tenant_id=tenant_id)
        merchant = await _add_image(
            db_session,
            product,
            url="https://cdn.example/merchant.png",
            position=0,
            alt_text="Mine",
            is_supplier=False,
        )
        supplier = await _add_image(
            db_session,
            product,
            url="https://cdn.example/supplier.png",
            position=1,
            is_supplier=True,
        )
        bodies = {
            merchant.url: _png((10, 10, 10)),
            supplier.url: _png((20, 20, 20)),
        }
        service = ImageAnalysisService(db_session, fetcher=_BytesFetcher(bodies))  # type: ignore[arg-type]
        await service.analyse_product_images(product.id)

        images = ProductImageRepository(db_session)
        await images.sync_for_product(product.id, [{"url": "https://cdn.example/new-supplier.png"}])
        live = await images.list_for_product(product.id)
        urls = {row.url: row for row in live}
        assert merchant.url in urls
        assert urls[merchant.url].is_supplier is False
        assert urls[merchant.url].alt_text == "Mine"
        assert supplier.url not in urls

    async def test_openai_unconfigured_keeps_deterministic_checks(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.ai, "provider", AIProviderName.OPENAI)
        _headers, tenant_id = await _register(client)
        product = await _seed_product(db_session, tenant_id=tenant_id)
        image = await _add_image(db_session, product, url="https://cdn.example/a.png", position=0)
        service = ImageAnalysisService(db_session, fetcher=_BytesFetcher(_png()))  # type: ignore[arg-type]

        report = await service.analyse_product_images(product.id)

        assert report.images[0].status == "checksOnly"
        assert image.analysis is not None
        assert image.analysis["errorCode"] == "AIProviderNotConfiguredError"
        assert image.analysis["captionProposal"] is None
        assert image.analysis["altTextProposal"] is None
        assert image.analysis["checks"]["blur"]["applicable"] is True
        assert image.analysis["checks"]["duplicates"]["contentSha256"]
        assert image.analysis["isSynthetic"] is None


class TestImageAnalysisTransaction:
    async def test_unexpected_error_rolls_back_flushed_execution_and_analysis(self) -> None:
        engine = create_async_engine(settings.database.async_dsn)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        tenant_id = uuid.uuid4()
        product_id = uuid.uuid4()
        slug = f"s6-{uuid.uuid4().hex[:12]}"

        try:
            async with factory() as session:
                session.add(Tenant(id=tenant_id, name="Stage 6 rollback", slug=slug))
                await session.flush()
                session.add(
                    Product(
                        id=product_id,
                        tenant_id=tenant_id,
                        source=ProductSource.MANUAL,
                        external_id=f"manual-{uuid.uuid4().hex[:12]}",
                        title="Rollback mug",
                        status=ProductStatus.DRAFT,
                    )
                )
                await session.flush()
                session.add(
                    ProductImage(
                        tenant_id=tenant_id,
                        product_id=product_id,
                        url="https://cdn.example/one.png",
                        position=0,
                    )
                )
                session.add(
                    ProductImage(
                        tenant_id=tenant_id,
                        product_id=product_id,
                        url="https://cdn.example/two.png",
                        position=1,
                    )
                )
                await session.commit()

            session = factory()
            try:
                set_tenant_id(tenant_id)
                service = ImageAnalysisService(
                    session,
                    fetcher=_BytesFetcher(_png()),  # type: ignore[arg-type]
                )
                calls = {"n": 0}
                original = service.prompts.execute_image_analysis

                async def _flaky(*args: Any, **kwargs: Any) -> tuple[str, object, object]:
                    calls["n"] += 1
                    if calls["n"] == 1:
                        return await original(*args, **kwargs)
                    raise TypeError("phase-c programming error")

                service.prompts.execute_image_analysis = _flaky  # type: ignore[method-assign]
                commits = 0
                real_commit = session.commit

                async def _count_commit() -> None:
                    nonlocal commits
                    commits += 1
                    await real_commit()

                session.commit = _count_commit  # type: ignore[method-assign]
                with pytest.raises(TypeError, match="phase-c programming error"):
                    await service.analyse_product_images(product_id)
                assert commits == 0
                await session.rollback()
            finally:
                await session.close()

            async with engine.connect() as conn:
                executions = (
                    await conn.execute(
                        text("SELECT count(*) FROM prompt_executions WHERE tenant_id = :tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                ).scalar_one()
                analysed = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM product_images "
                            "WHERE product_id = :product_id AND analysis IS NOT NULL"
                        ),
                        {"product_id": product_id},
                    )
                ).scalar_one()
            assert executions == 0
            assert analysed == 0
        finally:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})
            await engine.dispose()
