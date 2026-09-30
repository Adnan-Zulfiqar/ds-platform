"""Review finding E-1 — published AI text is never silently replaced.

Contract under test:

* A pipeline publish records ``content_source = ai_version`` and the exact
  version on the listing. The draft (``Product.title``/``description``) is
  untouched.
* Every later ordinary publish — editor HTTP route, service call, Celery
  task, retry — re-sends that AI title/body by default.
* Only an explicit ``replace_ai_content`` sends the draft text, and only then
  does the listing go back to ``content_source = product``.
* A failed publish changes nothing about what the listing says is live.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import clear_context, set_tenant_id
from app.core.exceptions import ConflictError
from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.sync import ShopifySyncService
from app.main import create_application
from app.models.product import ProductVersion
from app.models.shopify import ListingContentSource, StoreListing
from app.repositories.product import ProductRepository
from app.services.product_optimization import ProductOptimizationService
from app.services.product_pipeline import ProductPipelineService
from tests.integration.shopify_publish_live import (
    CountingPublishShopify,
    LivePublishTarget,
    live_publish_targets,
    own_publish_session,
)
from tests.integration.test_product_pipeline import _insert_pipeline_candidate
from tests.integration.test_shopify_publish_http_concurrency import _seed_publishable

pytestmark = pytest.mark.integration

AI_TITLE = "Approved AI title for E-1"
AI_BODY = "<p>Approved AI body</p>"


@pytest.fixture
def shopify_wire(monkeypatch: pytest.MonkeyPatch) -> CountingPublishShopify:
    wire = CountingPublishShopify()
    monkeypatch.setattr(
        shopify_service_module,
        "ShopifyClient",
        lambda **kwargs: wire.client(**kwargs),
    )
    return wire


async def _listing(live: LivePublishTarget) -> StoreListing:
    async with live.factory() as session:
        row = (
            await session.execute(
                sa.select(StoreListing).where(
                    StoreListing.tenant_id == live.tenant_id,
                    StoreListing.store_id == live.store_id,
                    StoreListing.product_id == live.product_id,
                )
            )
        ).scalar_one()
        return row


async def _publish_ai_version(live: LivePublishTarget) -> uuid.UUID:
    """Approve and pipeline-publish one real (non-synthetic) candidate."""
    async with own_publish_session(live) as session:
        product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
        # Real flow: the version-1 original snapshot exists before any candidate.
        await ProductOptimizationService(session)._ensure_original_snapshot(product)
        await session.refresh(product)
        version = await _insert_pipeline_candidate(
            session, product, title=AI_TITLE, description=AI_BODY, ai_provider="test"
        )
        await ProductPipelineService(session).approve(
            product.id, version_id=version.id, expected_updated_at=product.updated_at
        )
        await session.commit()
        await session.refresh(product)
        token = product.updated_at
        version_id = version.id
    async with own_publish_session(live) as session:
        await ProductPipelineService(session).publish(
            live.product_id,
            store_id=live.store_id,
            version_id=version_id,
            expected_updated_at=token,
        )
        await session.commit()
    return version_id


async def _ordinary_publish(live: LivePublishTarget, **kwargs: Any) -> dict[str, Any]:
    async with own_publish_session(live) as session:
        result = await ShopifySyncService(session).publish_product(
            store_id=live.store_id, product_id=live.product_id, **kwargs
        )
        await session.commit()
        return result


async def _edit_draft_title(live: LivePublishTarget, title: str) -> None:
    async with own_publish_session(live) as session:
        product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
        product.title = title
        await session.commit()


class TestPipelinePublishRecordsTheSource:
    async def test_listing_names_the_published_version_and_the_draft_is_untouched(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            version_id = await _publish_ai_version(live)
            listing = await _listing(live)
            async with live.factory() as session:
                set_tenant_id(live.tenant_id)
                product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
                draft_title = product.title
            clear_context()

        assert listing.content_source is ListingContentSource.AI_VERSION
        assert listing.content_version_id == version_id
        assert draft_title != AI_TITLE


class TestOrdinaryPublishPreservesLiveAiText:
    async def test_ordinary_publish_resends_the_ai_text_not_the_draft(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            version_id = await _publish_ai_version(live)
            await _edit_draft_title(live, "Merchant edited the draft title")
            result = await _ordinary_publish(live)
            listing = await _listing(live)

        body = shopify_wire.put_bodies[-1]
        assert body["title"] == AI_TITLE
        assert body["body_html"] == AI_BODY
        assert result["content_source"] == "ai_version"
        assert result["content_version_id"] == str(version_id)
        assert listing.content_source is ListingContentSource.AI_VERSION
        assert listing.content_version_id == version_id

    async def test_rolling_back_the_active_version_does_not_revert_the_store(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        """Activation is not publication. The store keeps what was published."""
        async with live_publish_targets() as (live,):
            version_id = await _publish_ai_version(live)
            async with own_publish_session(live) as session:
                original = (
                    await session.execute(
                        sa.select(ProductVersion).where(
                            ProductVersion.product_id == live.product_id,
                            ProductVersion.version_number == 1,
                        )
                    )
                ).scalar_one()
                await ProductOptimizationService(session).activate_version(
                    live.product_id, original.id
                )
                await session.commit()
            await _ordinary_publish(live)
            listing = await _listing(live)

        assert shopify_wire.put_bodies[-1]["title"] == AI_TITLE
        assert listing.content_version_id == version_id

    async def test_repeated_publishes_and_retries_keep_preserving(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            await _publish_ai_version(live)
            for _ in range(3):
                await _ordinary_publish(live)
            listing = await _listing(live)

        assert [body["title"] for body in shopify_wire.put_bodies[-3:]] == [AI_TITLE] * 3
        assert listing.content_source is ListingContentSource.AI_VERSION

    async def test_the_celery_publish_task_preserves_too(
        self, shopify_wire: CountingPublishShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.tasks.integrations import shopify as shopify_tasks

        loop = asyncio.get_running_loop()

        def bridge(coro: Any) -> Any:
            # Same bridge as `pipeline_bulk_harness.run_task`: the task's
            # coroutine runs on the test loop that owns the engine.
            return asyncio.run_coroutine_threadsafe(coro, loop).result()

        monkeypatch.setattr(shopify_tasks, "asyncio", SimpleNamespace(run=bridge))

        async with live_publish_targets() as (live,):
            version_id = await _publish_ai_version(live)
            await asyncio.to_thread(
                lambda: shopify_tasks.publish_product.apply(
                    args=(str(live.tenant_id), str(live.store_id), str(live.product_id)),
                    throw=True,
                ).get()
            )
            listing = await _listing(live)

        assert shopify_wire.put_bodies[-1]["title"] == AI_TITLE
        assert listing.content_version_id == version_id

    async def test_a_failed_publish_does_not_change_the_recorded_source(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            version_id = await _publish_ai_version(live)
            shopify_wire.fail_next_put_with = TimeoutError("simulated Shopify outage")
            with pytest.raises(TimeoutError):
                await _ordinary_publish(live, replace_ai_content=True)
            listing = await _listing(live)

        assert listing.content_source is ListingContentSource.AI_VERSION
        assert listing.content_version_id == version_id


class TestExplicitReplacement:
    async def test_replace_sends_the_draft_and_records_product_source(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            await _publish_ai_version(live)
            await _edit_draft_title(live, "Merchant chose the draft title")
            result = await _ordinary_publish(live, replace_ai_content=True)
            listing = await _listing(live)
            # And after replacement an ordinary publish keeps sending the draft.
            await _ordinary_publish(live)

        assert shopify_wire.put_bodies[-2]["title"] == "Merchant chose the draft title"
        assert shopify_wire.put_bodies[-1]["title"] == "Merchant chose the draft title"
        assert result["content_source"] == "product"
        assert result["content_version_id"] is None
        assert listing.content_source is ListingContentSource.PRODUCT
        assert listing.content_version_id is None

    async def test_replace_is_a_no_op_flag_when_the_store_shows_draft_text(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            await _ordinary_publish(live)
            result = await _ordinary_publish(live, replace_ai_content=True)
            listing = await _listing(live)

        assert result["content_source"] == "product"
        assert listing.content_source is ListingContentSource.PRODUCT


class TestDatabaseEnforcesTheSourcePair:
    async def test_ai_source_without_a_version_is_rejected(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            await _ordinary_publish(live)
            async with live.factory() as session:
                with pytest.raises(sa.exc.IntegrityError):
                    await session.execute(
                        sa.update(StoreListing)
                        .where(StoreListing.product_id == live.product_id)
                        .values(content_source=ListingContentSource.AI_VERSION)
                    )
                await session.rollback()

    async def test_a_version_of_another_product_cannot_be_recorded(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets(count=2) as (live_a, live_b):
            await _ordinary_publish(live_a)
            foreign_version_id = await _publish_ai_version(live_b)
            async with live_a.factory() as session:
                with pytest.raises(sa.exc.IntegrityError):
                    await session.execute(
                        sa.update(StoreListing)
                        .where(StoreListing.product_id == live_a.product_id)
                        .values(
                            content_source=ListingContentSource.AI_VERSION,
                            content_version_id=foreign_version_id,
                        )
                    )
                await session.rollback()


class TestFailClosedWhenTheLiveVersionIsUnreadable:
    async def test_unreadable_live_version_refuses_instead_of_sending_the_draft(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            version_id = await _publish_ai_version(live)
            puts_before = len(shopify_wire.put_bodies)
            async with live.factory() as session:
                # Simulate corruption the application itself never writes.
                await session.execute(
                    sa.update(ProductVersion)
                    .where(ProductVersion.id == version_id)
                    .values(content={"title": ""})
                )
                await session.commit()
            with pytest.raises(ConflictError) as raised:
                await _ordinary_publish(live)
            listing = await _listing(live)

        assert raised.value.details == {"reason": "published_ai_content_unavailable"}
        assert len(shopify_wire.put_bodies) == puts_before
        assert listing.content_version_id == version_id


PUBLISH_URL = "/api/v1/integrations/shopify/publish"


class TestHttpPublishRoute:
    async def test_editor_route_preserves_by_default_and_replaces_only_on_request(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        app = create_application()
        tenant_id: uuid.UUID | None = None
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                headers, meta = await _seed_publishable(client)
                tenant_id = meta["tenant_id"]
                product_id = meta["product_id"]
                store_id = meta["store_id"]

                first = await client.post(
                    PUBLISH_URL,
                    json={"productId": str(product_id), "storeId": str(store_id)},
                    headers=headers,
                )
                assert first.status_code == 200, first.text
                assert first.json()["contentSource"] == "product"

                # Approve + pipeline publish a real (non-synthetic) candidate.
                engine = create_async_engine(settings.database.async_dsn, poolclass=None)
                factory = async_sessionmaker(bind=engine, expire_on_commit=False)
                try:
                    async with factory() as session:
                        set_tenant_id(tenant_id)
                        product = await ProductRepository(session).get_by_id_or_raise(product_id)
                        version = await _insert_pipeline_candidate(
                            session, product, title=AI_TITLE, description=AI_BODY
                        )
                        await session.commit()
                        version_id = version.id
                        token = product.updated_at.isoformat()
                finally:
                    await engine.dispose()
                    clear_context()

                approved = await client.post(
                    f"/api/v1/products/{product_id}/pipeline/versions/{version_id}/approve",
                    json={"expectedUpdatedAt": token},
                    headers=headers,
                )
                assert approved.status_code == 200, approved.text
                published = await client.post(
                    f"/api/v1/products/{product_id}/pipeline/versions/{version_id}/publish",
                    json={
                        "storeId": str(store_id),
                        "expectedUpdatedAt": approved.json()["updatedAt"],
                    },
                    headers=headers,
                )
                assert published.status_code == 200, published.text
                assert published.json()["contentSource"] == "ai_version"
                assert published.json()["contentVersionId"] == str(version_id)

                listings = await client.get(
                    f"/api/v1/drafts/{product_id}/listings", headers=headers
                )
                assert listings.status_code == 200, listings.text
                assert listings.json()[0]["contentSource"] == "ai_version"
                assert listings.json()[0]["contentVersionId"] == str(version_id)

                kept = await client.post(
                    PUBLISH_URL,
                    json={"productId": str(product_id), "storeId": str(store_id)},
                    headers=headers,
                )
                assert kept.status_code == 200, kept.text
                assert kept.json()["contentSource"] == "ai_version"
                assert shopify_wire.put_bodies[-1]["title"] == AI_TITLE

                replaced = await client.post(
                    PUBLISH_URL,
                    json={
                        "productId": str(product_id),
                        "storeId": str(store_id),
                        "replaceAiContent": True,
                    },
                    headers=headers,
                )
                assert replaced.status_code == 200, replaced.text
                assert replaced.json()["contentSource"] == "product"
                assert replaced.json()["contentVersionId"] is None
                assert shopify_wire.put_bodies[-1]["title"] != AI_TITLE
        finally:
            if tenant_id is not None:
                engine = create_async_engine(settings.database.async_dsn)
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            sa.text("DELETE FROM tenants WHERE id = :id"),
                            {"id": str(tenant_id)},
                        )
                finally:
                    await engine.dispose()
