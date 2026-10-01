"""Live harness for Shopify publish concurrency (UX-L2B-R2).

Committed tenants/stores/products so two real PostgreSQL connections can
contend for the product row lock. Only the Shopify REST wire is faked —
barriers, not sleeps — so create counts prove what left the process.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import clear_context, set_tenant_id
from app.core.encryption import encrypt
from app.models.integration import IntegrationStatus
from app.models.product import Product, ProductSource, ProductStatus
from app.models.shopify import ShopifyConnection, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.tenant import Tenant
from tests.integration.live_locks import (
    HANG_GUARD_SECONDS,
    backend_pid,
    wait_until_blocked,
)


class CountingPublishShopify:
    """Fake Shopify REST Admin API for publish create/adopt/update."""

    def __init__(self) -> None:
        # Keyed by shop_domain so two stores never share a catalog.
        self.products_by_handle: dict[tuple[str, str], dict[str, Any]] = {}
        self.products_by_id: dict[tuple[str, str], dict[str, Any]] = {}
        self.creates: list[dict[str, Any]] = []
        self.puts: list[str] = []
        #: Product bodies sent by PUT, in order — what an update actually wrote.
        self.put_bodies: list[dict[str, Any]] = []
        self.gets: list[dict[str, Any]] = []
        self._next_id = 9000
        self.hold_first_create: asyncio.Event | None = None
        self.first_create_reached: asyncio.Event | None = None
        self.fail_next_create_with: Exception | None = None
        self.fail_next_put_with: Exception | None = None
        self.drop_create_response: bool = False
        self._create_hold_consumed = False

    def client(self, *, shop_domain: str, access_token: str, tenant_id: str) -> _FakeRestClient:
        return _FakeRestClient(self, shop_domain=shop_domain)

    async def _on_create(self, shop_domain: str, body: dict[str, Any]) -> dict[str, Any]:
        product_body = dict(body.get("product") or {})
        handle = str(product_body.get("handle") or "")
        if self.fail_next_create_with is not None:
            exc = self.fail_next_create_with
            self.fail_next_create_with = None
            raise exc

        hold = None
        if not self._create_hold_consumed and self.hold_first_create is not None:
            hold = self.hold_first_create
            self.hold_first_create = None
            self._create_hold_consumed = True
            if self.first_create_reached is not None:
                self.first_create_reached.set()
            await asyncio.wait_for(hold.wait(), timeout=HANG_GUARD_SECONDS)

        self._next_id += 1
        external_id = str(self._next_id)
        created = {
            "id": int(external_id),
            "handle": handle,
            "title": product_body.get("title"),
            "status": product_body.get("status") or "draft",
            "published_at": None,
            "variants": [
                {
                    "id": int(external_id) * 10,
                    "inventory_item_id": int(external_id) * 100,
                }
            ],
        }
        self.creates.append({"shop": shop_domain, "handle": handle, "id": external_id})
        self.products_by_handle[(shop_domain, handle)] = created
        self.products_by_id[(shop_domain, external_id)] = created
        if self.drop_create_response:
            self.drop_create_response = False
            raise TimeoutError("simulated lost provider response after create")
        return {"product": created}


@dataclass
class _FakeRestClient:
    wire: CountingPublishShopify
    shop_domain: str

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.wire.gets.append({"path": path, "params": params, "shop": self.shop_domain})
        if path.startswith("/products.json"):
            handle = str(params.get("handle") or "")
            found = self.wire.products_by_handle.get((self.shop_domain, handle))
            return {"products": [found] if found else []}
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        if path == "/products.json":
            return await self.wire._on_create(self.shop_domain, json_body or {})
        raise AssertionError(f"unexpected POST {path}")

    async def put(self, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        self.wire.puts.append(path)
        if self.wire.fail_next_put_with is not None:
            exc = self.wire.fail_next_put_with
            self.wire.fail_next_put_with = None
            raise exc
        external_id = path.strip("/").split("/")[1].removesuffix(".json")
        existing = self.wire.products_by_id.get((self.shop_domain, external_id))
        if existing is None:
            raise AssertionError(f"PUT for unknown product {external_id}")
        body = (json_body or {}).get("product") or {}
        self.wire.put_bodies.append(dict(body))
        existing = {**existing, **{k: v for k, v in body.items() if k != "variants"}}
        self.wire.products_by_id[(self.shop_domain, external_id)] = existing
        handle = str(existing.get("handle") or "")
        if handle:
            self.wire.products_by_handle[(self.shop_domain, handle)] = existing
        return {"product": existing}


@dataclass(frozen=True)
class LivePublishTarget:
    tenant_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    shop_domain: str
    updated_at: datetime
    factory: Callable[[], Any]
    second_store_id: uuid.UUID | None = None


@dataclass
class _Seeded:
    targets: list[LivePublishTarget] = field(default_factory=list)


@asynccontextmanager
async def live_publish_targets(
    *,
    count: int = 1,
    with_second_store: bool = False,
) -> AsyncIterator[list[LivePublishTarget]]:
    """Commit N publishable drafts with connected Shopify stores; drop after."""
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    created: list[LivePublishTarget] = []
    try:
        for _ in range(count):
            suffix = uuid.uuid4().hex[:10]
            shop_domain = f"pub-{suffix}.myshopify.com"
            async with factory() as session:
                tenant = Tenant(name=f"Publish {suffix}", slug=f"pub-{suffix}")
                session.add(tenant)
                await session.flush()
                store = Store(
                    tenant_id=tenant.id,
                    name=f"Store {suffix}",
                    slug=f"store-{suffix}",
                    platform=StorePlatform.SHOPIFY,
                    status=StoreStatus.CONNECTED,
                    currency="GBP",
                    settings={"countryCode": "GB"},
                )
                session.add(store)
                await session.flush()
                second_store_id: uuid.UUID | None = None
                if with_second_store:
                    store_b = Store(
                        tenant_id=tenant.id,
                        name=f"Store B {suffix}",
                        slug=f"store-b-{suffix}",
                        platform=StorePlatform.SHOPIFY,
                        status=StoreStatus.CONNECTED,
                        currency="GBP",
                        settings={"countryCode": "GB"},
                    )
                    session.add(store_b)
                    await session.flush()
                    second_store_id = store_b.id
                    session.add(
                        ShopifyConnection(
                            tenant_id=tenant.id,
                            store_id=store_b.id,
                            shop_domain=f"pub-b-{suffix}.myshopify.com",
                            encrypted_access_token=encrypt(f"shpat_b_{suffix}"),
                            scopes="write_products",
                            status=IntegrationStatus.CONNECTED,
                        )
                    )
                product = Product(
                    tenant_id=tenant.id,
                    source=ProductSource.MANUAL,
                    external_id=f"manual-{suffix}",
                    title=f"Publishable draft {suffix}",
                    status=ProductStatus.DRAFT,
                    description="<p>Ready body</p>",
                    supplier_description="<p>Ready body</p>",
                    supplier_title=f"Publishable draft {suffix}",
                    import_ship_to_country="GB",
                )
                session.add(product)
                await session.flush()
                session.add(
                    ShopifyConnection(
                        tenant_id=tenant.id,
                        store_id=store.id,
                        shop_domain=shop_domain,
                        encrypted_access_token=encrypt(f"shpat_{suffix}"),
                        scopes="write_products",
                        status=IntegrationStatus.CONNECTED,
                    )
                )
                await session.commit()
                created.append(
                    LivePublishTarget(
                        tenant_id=tenant.id,
                        store_id=store.id,
                        product_id=product.id,
                        shop_domain=shop_domain,
                        updated_at=product.updated_at
                        if product.updated_at.tzinfo
                        else product.updated_at.replace(tzinfo=UTC),
                        factory=factory,
                        second_store_id=second_store_id,
                    )
                )
        yield created
    finally:
        async with factory() as cleanup:
            for live in created:
                await cleanup.execute(sa.delete(Tenant).where(Tenant.id == live.tenant_id))
            await cleanup.commit()
        await engine.dispose()
        clear_context()


@asynccontextmanager
async def own_publish_session(live: LivePublishTarget) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            set_tenant_id(live.tenant_id)
            yield session
    finally:
        await engine.dispose()


async def listing_count(live: LivePublishTarget, *, store_id: uuid.UUID | None = None) -> int:
    sid = store_id or live.store_id
    async with live.factory() as session:
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(StoreListing)
            .where(
                StoreListing.tenant_id == live.tenant_id,
                StoreListing.store_id == sid,
                StoreListing.product_id == live.product_id,
                StoreListing.deleted_at.is_(None),
            )
        )
        return int(result.scalar_one())


__all__ = [
    "HANG_GUARD_SECONDS",
    "CountingPublishShopify",
    "LivePublishTarget",
    "backend_pid",
    "listing_count",
    "live_publish_targets",
    "own_publish_session",
    "wait_until_blocked",
]
