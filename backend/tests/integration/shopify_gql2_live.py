"""Shared harness for the GQL-2 integration tests.

Committing a tenant, a store and a connected Shopify integration takes enough
setup that repeating it in each test would bury the behaviour being asserted, so
it lives here — the same split ``rule_application_live`` uses.

**Committed, not held in a rolled-back transaction.** The concurrency tests need
a *different* PostgreSQL connection to see the row and contend for it, which a
shared test transaction would make impossible.

**Blocking is observed, never timed** — see ``live_locks``, which holds the
lock-contention helpers. They were extracted there when the eBay refresh tests
needed the same rendezvous; they are re-exported below so these imports keep
working.

**Only Shopify's wire is faked.** ``CountingShopify`` answers HTTP. The service,
the repository, the row lock, the GraphQL client, the document parser and the
reconciler are all production code, so a count of mutations is a count of what
actually left the process.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import clear_context, set_tenant_id
from app.core.encryption import encrypt
from app.integrations.shopify.graphql import ShopifyGraphQLClient
from app.models.integration import IntegrationStatus
from app.models.shopify import ShopifyConnection
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.tenant import Tenant
from tests.integration.live_locks import (
    HANG_GUARD_SECONDS,
    backend_pid,
    wait_until_blocked,
)


class CountingShopify:
    """Shopify's wire, counting every call and gated for rendezvous.

    ``hold_first_list`` holds whichever caller lists first — after it has taken
    the connection row — so another is forced into genuine lock contention
    instead of losing a race by luck. The listing snapshot is taken *before* the
    wait, which is what lets an unlocked control test model the real failure:
    both callers saw an empty shop.
    """

    def __init__(self, *, currency: str = "GBP") -> None:
        self.currency: Any = currency
        self.subscriptions: list[dict[str, Any]] = []
        self.creates: list[dict[str, Any]] = []
        self.lists = 0
        self.currency_reads = 0
        self.paths: list[str] = []
        self.operations: list[str | None] = []
        self.hold_first_list: asyncio.Event | None = None
        self.first_list_reached: asyncio.Event | None = None
        self.fail_currency_with: Exception | None = None
        self._next_id = 1000

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        body = json.loads(request.content)
        name = body.get("operationName")
        self.operations.append(name)

        if name == "ShopAuthority":
            self.currency_reads += 1
            if self.fail_currency_with is not None:
                raise self.fail_currency_with
            return httpx.Response(200, json={"data": {"shop": {"currencyCode": self.currency}}})

        if name == "WebhookSubscriptions":
            snapshot = list(self.subscriptions)
            self.lists += 1
            hold, self.hold_first_list = self.hold_first_list, None
            if hold is not None:
                if self.first_list_reached is not None:
                    self.first_list_reached.set()
                await asyncio.wait_for(hold.wait(), timeout=HANG_GUARD_SECONDS)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptions": {
                            "nodes": snapshot,
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                },
            )

        if name == "WebhookSubscriptionCreate":
            variables = body["variables"]
            self.creates.append(variables)
            self._next_id += 1
            node = {
                "id": f"gid://shopify/WebhookSubscription/{self._next_id}",
                "topic": variables["topic"],
                "uri": variables["webhookSubscription"]["uri"],
                "format": variables["webhookSubscription"]["format"],
                "includeFields": [],
                "filter": None,
            }
            self.subscriptions.append(node)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptionCreate": {
                            "webhookSubscription": node,
                            "userErrors": [],
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected operation {name!r}")

    def creates_for(self, topic_enum: str) -> list[dict[str, Any]]:
        return [call for call in self.creates if call["topic"] == topic_enum]

    def client(self, *, shop_domain: str, access_token: str, **_: Any) -> ShopifyGraphQLClient:
        """Stand in for the production constructor: wire faked, rest real."""
        return ShopifyGraphQLClient(
            shop_domain=shop_domain,
            access_token=access_token,
            transport=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


@dataclass(frozen=True)
class LiveStore:
    tenant_id: uuid.UUID
    store_id: uuid.UUID
    shop_domain: str
    factory: Callable[[], AsyncSession]


@asynccontextmanager
async def live_stores(count: int = 1) -> AsyncIterator[list[LiveStore]]:
    """Commit N tenants, each with a connected Shopify store; drop them after.

    Cleanup is one tenant delete per store, which cascades.
    """
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    created: list[LiveStore] = []
    try:
        for _ in range(count):
            suffix = uuid.uuid4().hex[:10]
            shop_domain = f"gql2-{suffix}.myshopify.com"
            async with factory() as session:
                tenant = Tenant(name=f"GQL2 {suffix}", slug=f"gql2-{suffix}")
                session.add(tenant)
                await session.flush()
                store = Store(
                    tenant_id=tenant.id,
                    name=shop_domain,
                    slug=f"store-{suffix}",
                    platform=StorePlatform.SHOPIFY,
                    status=StoreStatus.CONNECTED,
                    storefront_url=f"https://{shop_domain}",
                    external_store_id=shop_domain,
                )
                session.add(store)
                await session.flush()
                session.add(
                    ShopifyConnection(
                        tenant_id=tenant.id,
                        store_id=store.id,
                        shop_domain=shop_domain,
                        encrypted_access_token=encrypt(f"shpat_not_real_{suffix}"),
                        scopes="read_products,read_orders,read_inventory",
                        status=IntegrationStatus.CONNECTED,
                    )
                )
                await session.commit()
                created.append(
                    LiveStore(
                        tenant_id=tenant.id,
                        store_id=store.id,
                        shop_domain=shop_domain,
                        factory=factory,
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
async def own_connection(live: LiveStore) -> AsyncIterator[AsyncSession]:
    """A session on a private engine, with tenant context bound."""
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            set_tenant_id(live.tenant_id)
            yield session
    finally:
        await engine.dispose()


#: Re-exported from ``live_locks`` — the GQL-2 tests import them from here.
__all__ = [
    "HANG_GUARD_SECONDS",
    "CountingShopify",
    "LiveStore",
    "backend_pid",
    "live_stores",
    "own_connection",
    "wait_until_blocked",
]
