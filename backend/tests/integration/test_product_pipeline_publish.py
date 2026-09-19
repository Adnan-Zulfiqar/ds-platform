"""Live Shopify overlay publish for Stage 7 — committed rows, real locks."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.core.exceptions import ConflictError, ShopifyPublishBusyError, ValidationError
from app.integrations.shopify import service as shopify_service_module
from app.models.product import Product
from app.repositories.product import ProductRepository, ProductVersionRepository
from app.services.product_pipeline import PIPELINE_PUBLISH_TITLE_MAX, ProductPipelineService
from tests.integration.live_locks import HANG_GUARD_SECONDS, backend_pid, wait_until_blocked
from tests.integration.shopify_publish_live import (
    CountingPublishShopify,
    LivePublishTarget,
    listing_count,
    live_publish_targets,
    own_publish_session,
)
from tests.integration.test_product_pipeline import _insert_pipeline_candidate

pytestmark = pytest.mark.integration

_RAW_HTML = (
    "<p>Keep</p><script>alert(1)</script>"
    '<img src="https://cdn.example/a.jpg" onerror="steal()">'
    '<a href="javascript:alert(1)">bad</a>'
    "<strong>bold</strong>"
)


@pytest.fixture
def shopify_wire(monkeypatch: pytest.MonkeyPatch) -> CountingPublishShopify:
    wire = CountingPublishShopify()
    monkeypatch.setattr(
        shopify_service_module,
        "ShopifyClient",
        lambda **kwargs: wire.client(**kwargs),
    )
    return wire


def _capture_creates(wire: CountingPublishShopify) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    original = wire._on_create

    async def _wrapped(shop_domain: str, body: dict[str, Any]) -> dict[str, Any]:
        captured.append(body)
        return await original(shop_domain, body)

    wire._on_create = _wrapped  # type: ignore[method-assign]
    return captured


async def _approve_fixture(
    session: Any,
    live: LivePublishTarget,
    **candidate_kwargs: Any,
) -> tuple[Product, Any]:
    products = ProductRepository(session)
    product = await products.get_by_id_or_raise(live.product_id)
    product.seo_title = "Merchant SEO title"
    product.seo_description = "Merchant SEO description"
    product.tags = ["keep-me"]
    await session.flush()
    await session.refresh(product)
    version = await _insert_pipeline_candidate(session, product, **candidate_kwargs)
    await ProductPipelineService(session).approve(
        product.id,
        version_id=version.id,
        expected_updated_at=product.updated_at,
    )
    await session.flush()
    await session.refresh(product)
    return product, version


class TestPipelineOverlayPublish:
    async def test_overlay_sends_ai_title_and_sanitized_body_not_ai_seo(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        captured = _capture_creates(shopify_wire)
        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                product, version = await _approve_fixture(
                    session,
                    live,
                    title="Approved AI title",
                    description=_RAW_HTML,
                    is_synthetic=False,
                    ai_provider="test",
                )
                merchant_description = product.description
                await session.commit()
                token = product.updated_at
                version_id = version.id

            async with own_publish_session(live) as session:
                result = await ProductPipelineService(session).publish(
                    live.product_id,
                    store_id=live.store_id,
                    version_id=version_id,
                    expected_updated_at=token,
                )
                await session.commit()
                reloaded = await ProductVersionRepository(session).get_by_id_for_product(
                    product_id=live.product_id, version_id=version_id
                )
                live_product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
            listings = await listing_count(live)

        assert result["external_product_id"]
        assert listings == 1
        assert captured, "expected a Shopify create payload"
        body = captured[0]["product"]
        assert body["title"] == "Approved AI title"
        assert "<script>" not in body["body_html"]
        assert "alert(1)" not in body["body_html"]
        assert "onerror" not in body["body_html"]
        assert "javascript:" not in body["body_html"]
        assert "<p>Keep</p>" in body["body_html"]
        assert "<strong>bold</strong>" in body["body_html"]
        assert body.get("metafields_global_title_tag") == "Merchant SEO title"
        assert body.get("metafields_global_description_tag") == "Merchant SEO description"
        assert "keep-me" in body["tags"]
        assert "AI SEO TITLE MUST NOT PUBLISH" not in str(body)
        assert "ai,must,not,become,tags" not in body["tags"]
        assert reloaded is not None
        assert reloaded.active is True
        assert reloaded.content["description"] == _RAW_HTML
        assert live_product.description == merchant_description
        assert live_product.title != "Approved AI title"

    async def test_shopify_failure_does_not_deactivate_the_candidate(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        shopify_wire.fail_next_create_with = RuntimeError("boom")
        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                product, version = await _approve_fixture(
                    session, live, is_synthetic=False, ai_provider="test"
                )
                await session.commit()
                token = product.updated_at
                version_id = version.id

            async with own_publish_session(live) as session:
                with pytest.raises(RuntimeError, match="boom"):
                    await ProductPipelineService(session).publish(
                        live.product_id,
                        store_id=live.store_id,
                        version_id=version_id,
                        expected_updated_at=token,
                    )

            async with own_publish_session(live) as session:
                reloaded = await ProductVersionRepository(session).get_by_id_for_product(
                    product_id=live.product_id, version_id=version_id
                )
                live_product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
            listings = await listing_count(live)

        assert reloaded is not None
        assert reloaded.active is True
        assert live_product.optimized_title == "Approved AI title"
        assert listings == 0

    async def test_merchant_edit_after_approve_uses_m2a_then_live_merchant_fields(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        captured = _capture_creates(shopify_wire)
        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                product, version = await _approve_fixture(
                    session, live, is_synthetic=False, ai_provider="test"
                )
                await session.commit()
                stale_token = product.updated_at
                version_id = version.id

            async with own_publish_session(live) as session:
                live_product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
                await session.execute(
                    sa.update(Product)
                    .where(Product.id == live.product_id)
                    .values(
                        seo_title="Edited merchant SEO",
                        tags=["edited-tag"],
                        updated_at=live_product.updated_at + timedelta(seconds=5),
                    )
                )
                await session.commit()
                await session.refresh(live_product)
                current_token = live_product.updated_at

            async with own_publish_session(live) as session:
                with pytest.raises(ConflictError) as stale:
                    await ProductPipelineService(session).publish(
                        live.product_id,
                        store_id=live.store_id,
                        version_id=version_id,
                        expected_updated_at=stale_token,
                    )
                assert stale.value.details.get("reason") == "draft_version_stale"

            async with own_publish_session(live) as session:
                result = await ProductPipelineService(session).publish(
                    live.product_id,
                    store_id=live.store_id,
                    version_id=version_id,
                    expected_updated_at=current_token,
                )
                await session.commit()

        assert result["external_product_id"]
        body = captured[0]["product"]
        assert body["title"] == "Approved AI title"
        assert body.get("metafields_global_title_tag") == "Edited merchant SEO"
        assert "edited-tag" in body["tags"]

    async def test_title_255_is_publishable(self, shopify_wire: CountingPublishShopify) -> None:
        title = "a" * PIPELINE_PUBLISH_TITLE_MAX
        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                product, version = await _approve_fixture(
                    session, live, title=title, is_synthetic=False, ai_provider="test"
                )
                await session.commit()
                token = product.updated_at
                version_id = version.id
            async with own_publish_session(live) as session:
                result = await ProductPipelineService(session).publish(
                    live.product_id,
                    store_id=live.store_id,
                    version_id=version_id,
                    expected_updated_at=token,
                )
                await session.commit()
        assert result["external_product_id"]


class TestPublishActiveStateTocTou:
    async def test_sibling_approval_before_lock_rejects_stale_overlay(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        """B commits while A waits for the Product lock; A must not reach Shopify."""
        holder_ready = asyncio.Event()
        release_holder = asyncio.Event()
        publish_entered = asyncio.Event()
        publish_pid: list[int] = []

        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                product, candidate_a = await _approve_fixture(
                    session,
                    live,
                    title="Candidate A",
                    is_synthetic=False,
                    ai_provider="test",
                )
                await session.commit()
                a_id = candidate_a.id
                token = product.updated_at

            async with own_publish_session(live) as session:
                product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
                candidate_b = await _insert_pipeline_candidate(
                    session,
                    product,
                    title="Candidate B",
                    is_synthetic=False,
                    ai_provider="test",
                )
                await session.commit()
                b_id = candidate_b.id

            async def _hold_then_approve_b() -> None:
                async with own_publish_session(live) as session:
                    locked = await ProductRepository(session).lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release_holder.wait(), timeout=HANG_GUARD_SECONDS)
                    await ProductPipelineService(session).approve(
                        live.product_id,
                        version_id=b_id,
                        expected_updated_at=locked.updated_at,
                    )
                    await session.commit()

            async def _publish_a() -> Any:
                async with own_publish_session(live) as session:
                    publish_pid.append(await backend_pid(session))
                    publish_entered.set()
                    return await ProductPipelineService(session).publish(
                        live.product_id,
                        store_id=live.store_id,
                        version_id=a_id,
                        expected_updated_at=token,
                    )

            holder = asyncio.create_task(_hold_then_approve_b())
            await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)
            publisher = asyncio.create_task(_publish_a())
            await asyncio.wait_for(publish_entered.wait(), timeout=HANG_GUARD_SECONDS)
            await wait_until_blocked(live.factory, publish_pid[0])
            release_holder.set()
            await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)
            with pytest.raises(ValidationError) as exc_info:
                await asyncio.wait_for(publisher, timeout=HANG_GUARD_SECONDS)

        assert exc_info.value.details.get("reason") == "candidate_not_approved"
        assert shopify_wire.creates == []
        assert shopify_wire.gets == []

    async def test_publish_holding_the_lock_blocks_sibling_approval(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        captured = _capture_creates(shopify_wire)
        release = asyncio.Event()
        shopify_wire.hold_first_create = release
        shopify_wire.first_create_reached = asyncio.Event()
        approve_pid: list[int] = []
        approve_entered = asyncio.Event()

        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                product, candidate_a = await _approve_fixture(
                    session,
                    live,
                    title="Candidate A",
                    is_synthetic=False,
                    ai_provider="test",
                )
                await session.commit()
                a_id = candidate_a.id
                token = product.updated_at

            async with own_publish_session(live) as session:
                product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
                candidate_b = await _insert_pipeline_candidate(
                    session,
                    product,
                    title="Candidate B",
                    is_synthetic=False,
                    ai_provider="test",
                )
                await session.commit()
                b_id = candidate_b.id
                b_token = product.updated_at

            async def _publish_a() -> Any:
                async with own_publish_session(live) as session:
                    result = await ProductPipelineService(session).publish(
                        live.product_id,
                        store_id=live.store_id,
                        version_id=a_id,
                        expected_updated_at=token,
                    )
                    await session.commit()
                    return result

            async def _approve_b() -> Product:
                async with own_publish_session(live) as session:
                    approve_pid.append(await backend_pid(session))
                    approve_entered.set()
                    approved = await ProductPipelineService(session).approve(
                        live.product_id,
                        version_id=b_id,
                        expected_updated_at=b_token,
                    )
                    await session.commit()
                    return approved

            publisher = asyncio.create_task(_publish_a())
            await asyncio.wait_for(
                shopify_wire.first_create_reached.wait(), timeout=HANG_GUARD_SECONDS
            )
            approver = asyncio.create_task(_approve_b())
            await asyncio.wait_for(approve_entered.wait(), timeout=HANG_GUARD_SECONDS)
            await wait_until_blocked(live.factory, approve_pid[0])
            release.set()
            published = await asyncio.wait_for(publisher, timeout=HANG_GUARD_SECONDS)
            await asyncio.wait_for(approver, timeout=HANG_GUARD_SECONDS)

        assert published["external_product_id"]
        assert captured[0]["product"]["title"] == "Candidate A"

    async def test_lock_timeout_returns_shopify_publish_busy(
        self, shopify_wire: CountingPublishShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.integrations.shopify.sync.PUBLISH_LOCK_TIMEOUT_MS", 50)
        release = asyncio.Event()
        holder_ready = asyncio.Event()
        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                product, version = await _approve_fixture(
                    session, live, is_synthetic=False, ai_provider="test"
                )
                await session.commit()
                token = product.updated_at
                version_id = version.id

            async def _hold() -> None:
                async with own_publish_session(live) as session:
                    locked = await ProductRepository(session).lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release.wait(), timeout=HANG_GUARD_SECONDS)

            holder = asyncio.create_task(_hold())
            await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)
            async with own_publish_session(live) as session:
                with pytest.raises(ShopifyPublishBusyError) as exc_info:
                    await ProductPipelineService(session).publish(
                        live.product_id,
                        store_id=live.store_id,
                        version_id=version_id,
                        expected_updated_at=token,
                    )
            assert exc_info.value.code == "shopify_publish_busy"
            release.set()
            await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)
        assert shopify_wire.creates == []
