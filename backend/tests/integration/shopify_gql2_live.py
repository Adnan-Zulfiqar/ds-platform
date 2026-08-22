"""Shared harness for the GQL-2 integration tests.

Committing a tenant, a store and a connected Shopify integration takes enough
setup that repeating it in each test would bury the behaviour being asserted, so
it lives here — the same split ``rule_application_live`` uses.

**Committed, not held in a rolled-back transaction.** The concurrency tests need
a *different* PostgreSQL connection to see the row and contend for it, which a
shared test transaction would make impossible.

**Blocking is observed, never timed.** ``pg_blocking_pids()`` is PostgreSQL's
own answer to "is this backend waiting on somebody else's lock", so a test can
wait for that fact rather than for a sleep that is generous enough today and
flaky on a loaded machine tomorrow. The timeout here exists only so a genuine
hang fails the suite instead of stalling it; it is never the thing asserted.

**Only Shopify's wire is faked.** ``CountingShopify`` answers HTTP. The service,
the repository, the row lock, the GraphQL client, the document parser and the
reconciler are all production code, so a count of mutations is a count of what
actually left the process.
"""

from __future__ import annotations

import asyncio
import json
import time
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

#: Upper bound on how long a helper waits for a state that should arrive in
#: milliseconds. Reaching it means something is genuinely stuck — a failure
#: worth seeing rather than a slow machine to accommodate.
HANG_GUARD_SECONDS = 30.0


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


async def backend_pid(session: AsyncSession) -> int:
    return int((await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())


async def wait_until_blocked(factory: Callable[[], AsyncSession], pid: int) -> list[int]:
    """Wait for PostgreSQL to report ``pid`` waiting on somebody else's lock.

    Polling this turns a race into a rendezvous: the test moves on the instant
    contention is real, and fails outright if contention never happens.
    """
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while time.monotonic() < deadline:
        async with factory() as observer:
            blockers = (
                await observer.execute(sa.text("SELECT pg_blocking_pids(:p)"), {"p": pid})
            ).scalar_one()
        if blockers:
            return list(blockers)
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} never blocked — no lock contention occurred")
