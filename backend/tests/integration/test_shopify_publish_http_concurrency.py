"""UX-L2B-R3 — concurrent publishes via the real HTTP endpoint.

Uses the production FastAPI app and ASGI transport so each request gets its
own database session — the same shape as two browser tabs — with Shopify REST
faked behind barriers.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.context import set_tenant_id
from app.core.encryption import encrypt
from app.integrations.shopify import service as shopify_service_module
from app.main import create_application
from app.models.integration import IntegrationStatus
from app.models.product import Product, ProductSource, ProductStatus
from app.models.shopify import ShopifyConnection, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.conftest import registration_payload
from tests.integration.live_locks import HANG_GUARD_SECONDS
from tests.integration.shopify_publish_live import CountingPublishShopify

pytestmark = pytest.mark.integration

PUBLISH_URL = "/api/v1/integrations/shopify/publish"


@pytest.fixture
def shopify_wire(monkeypatch: pytest.MonkeyPatch) -> CountingPublishShopify:
    wire = CountingPublishShopify()
    monkeypatch.setattr(
        shopify_service_module,
        "ShopifyClient",
        lambda **kwargs: wire.client(**kwargs),
    )
    return wire


async def _seed_publishable(client: AsyncClient) -> tuple[dict[str, str], dict[str, Any]]:
    """Register, then insert a connected store + draft outside the request txn."""
    registered = await client.post("/api/v1/auth/register", json=registration_payload())
    assert registered.status_code == 201, registered.text
    body = registered.json()
    tenant_id = uuid.UUID(body["identity"]["tenant"]["id"])
    headers = {"Authorization": f"Bearer {body['tokens']['accessToken']}"}

    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            set_tenant_id(tenant_id)
            suffix = uuid.uuid4().hex[:10]
            store = Store(
                tenant_id=tenant_id,
                name=f"HTTP Store {suffix}",
                slug=f"http-{suffix}",
                platform=StorePlatform.SHOPIFY,
                status=StoreStatus.CONNECTED,
                currency="GBP",
                settings={"countryCode": "GB"},
            )
            session.add(store)
            await session.flush()
            product = Product(
                tenant_id=tenant_id,
                source=ProductSource.MANUAL,
                external_id=f"manual-{suffix}",
                title=f"HTTP publish draft {suffix}",
                status=ProductStatus.DRAFT,
                description="<p>Ready</p>",
                supplier_description="<p>Ready</p>",
                supplier_title=f"HTTP publish draft {suffix}",
                import_ship_to_country="GB",
            )
            session.add(product)
            await session.flush()
            session.add(
                ShopifyConnection(
                    tenant_id=tenant_id,
                    store_id=store.id,
                    shop_domain=f"http-{suffix}.myshopify.com",
                    encrypted_access_token=encrypt(f"shpat_{suffix}"),
                    scopes="write_products",
                    status=IntegrationStatus.CONNECTED,
                )
            )
            await session.commit()
            updated = product.updated_at
            if updated.tzinfo is None:
                iso = updated.isoformat() + "Z"
            else:
                iso = updated.isoformat().replace("+00:00", "Z")
            meta = {
                "tenant_id": tenant_id,
                "store_id": store.id,
                "product_id": product.id,
                "updated_at": iso,
            }
    finally:
        await engine.dispose()

    return headers, meta


class TestHttpConcurrentPublish:
    @pytest.mark.parametrize("run", range(3))
    async def test_two_http_publishes_create_exactly_one_remote_product(
        self, shopify_wire: CountingPublishShopify, run: int
    ) -> None:
        release = asyncio.Event()
        shopify_wire.hold_first_create = release
        shopify_wire.first_create_reached = asyncio.Event()

        app = create_application()
        tenant_id: uuid.UUID | None = None
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                headers, meta = await _seed_publishable(client)
                tenant_id = meta["tenant_id"]
                payload = {
                    "productId": str(meta["product_id"]),
                    "storeId": str(meta["store_id"]),
                    "expectedUpdatedAt": meta["updated_at"],
                }

                async def _post() -> Any:
                    return await client.post(PUBLISH_URL, json=payload, headers=headers)

                first = asyncio.create_task(_post())
                await asyncio.wait_for(
                    shopify_wire.first_create_reached.wait(), timeout=HANG_GUARD_SECONDS
                )
                second = asyncio.create_task(_post())
                # Second may block on product FOR UPDATE until first finishes create+commit.
                await asyncio.sleep(0.1)
                release.set()
                results = await asyncio.wait_for(
                    asyncio.gather(first, second), timeout=HANG_GUARD_SECONDS
                )

            statuses = sorted(r.status_code for r in results)
            assert all(s in (200, 409) for s in statuses), (
                f"run={run} unexpected statuses={statuses} bodies={[r.text for r in results]}"
            )
            assert len(shopify_wire.creates) == 1, f"run={run} creates={shopify_wire.creates}"
            success = [r for r in results if r.status_code == 200]
            assert success, f"run={run} no successful publish: {[r.text for r in results]}"
            for r in success:
                body = r.json()
                assert body.get("externalProductId")
                assert r.headers.get("x-request-id") or body.get("requestId")
                text_l = r.text.lower()
                assert "shpat_" not in text_l
                assert "access_token" not in text_l

            engine = create_async_engine(settings.database.async_dsn)
            try:
                async with engine.connect() as conn:
                    count = (
                        await conn.execute(
                            select(StoreListing.id).where(
                                StoreListing.tenant_id == meta["tenant_id"],
                                StoreListing.store_id == meta["store_id"],
                                StoreListing.product_id == meta["product_id"],
                                StoreListing.deleted_at.is_(None),
                            )
                        )
                    ).all()
                    assert len(count) == 1
            finally:
                await engine.dispose()
        finally:
            if tenant_id is not None:
                engine = create_async_engine(settings.database.async_dsn)
                try:
                    async with engine.begin() as conn:
                        await conn.execute(
                            text("DELETE FROM tenants WHERE id = :id"),
                            {"id": str(tenant_id)},
                        )
                finally:
                    await engine.dispose()
