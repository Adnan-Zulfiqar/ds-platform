"""Shared harness for the EBAY-C1 integration tests.

**Committed tenants, not a rolled-back transaction.** The refresh-serialisation
tests need a *second* PostgreSQL connection to see the connection row and
contend for its lock, which a shared test transaction makes impossible. The same
shape ``shopify_gql2_live`` uses, for the same reason.

**Only eBay's wire is faked.** ``FakeEbay`` answers HTTP at the token and
identity endpoints. The service, the repository, the row lock, the Redis state
store, the Fernet encryption and the migrations are all production code — so a
count of token requests is a count of what actually left the process, and a
value read out of a column is the value production would have stored.

**No eBay credential is used and no live eBay request is made by anything that
imports this module.** The client id, certificate id and RuName below are
obvious fakes, and every outbound call is intercepted by ``MockTransport``; an
unexpected URL raises rather than escaping to the network.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import EbayEnvironment, settings
from app.core.context import clear_context, set_tenant_id
from app.models.ebay import EbayConnection
from app.models.tenant import Tenant, TenantStatus
from tests.integration.live_locks import HANG_GUARD_SECONDS

#: Not shaped like a real eBay token beyond the ``v^1.1#i^1`` prefix, which is
#: there so the encoding assertions exercise the reserved characters that
#: actually appear in eBay's values.
ACCESS_TOKEN = "v^1.1#i^1#ACCESS-NOT-A-REAL-EBAY-TOKEN"
REFRESHED_ACCESS_TOKEN = "v^1.1#i^1#ACCESS-NOT-A-REAL-EBAY-TOKEN-ROTATED"
REFRESH_TOKEN = "v^1.1#i^1#REFRESH-NOT-A-REAL-EBAY-TOKEN"

#: eBay's immutable ``userId``. The username beside it is mutable on purpose —
#: several tests turn on the difference.
SELLER_USER_ID = "immutable-ebay-user-id-0001"
SELLER_USERNAME = "droppilot-test-seller"


class FakeEbay:
    """eBay's OAuth token service and Identity API, recorded rather than reached.

    Every request is kept, because the failures this integration is most likely
    to have are about what was *sent*: the grant type, the encoding of the
    authorization code, the ``redirect_uri`` carrying a RuName rather than a URL.

    ``hold_next_refresh`` holds whichever caller reaches the refresh grant first
    — after it has taken the row lock — so a second caller is forced into
    genuine lock contention instead of losing a race by luck.

    ``refresh_reached`` fires when a held caller arrives; ``second_refresh_seen``
    fires when a *second* refresh arrives at all. Both are events rather than
    counters a test polls: a poll loop turns "did this happen" into "did this
    happen within the time I guessed", which is how concurrency tests become
    flaky on a loaded machine.
    """

    def __init__(self) -> None:
        self.token_requests: list[dict[str, str]] = []
        self.identity_calls = 0

        self.token_status = 200
        self.token_error = "invalid_grant"
        self.identity_status = 200

        self.seller_user_id = SELLER_USER_ID
        self.username: str | None = SELLER_USERNAME
        self.account_type: str | None = "BUSINESS"
        self.marketplace_id: str | None = "EBAY_GB"

        self.access_expires_in = 7200
        self.issue_refresh_token = True
        self.granted_scope: str | None = None

        self.hold_next_refresh: asyncio.Event | None = None
        self.refresh_reached: asyncio.Event | None = None
        self.second_refresh_seen: asyncio.Event | None = None

    # --- counters ----------------------------------------------------------

    def grants(self, grant_type: str) -> list[dict[str, str]]:
        return [r for r in self.token_requests if r.get("grant_type") == grant_type]

    @property
    def refresh_count(self) -> int:
        return len(self.grants("refresh_token"))

    # --- the wire ----------------------------------------------------------

    async def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if "/identity/v1/oauth2/token" in url:
            return await self._token(request)
        if "/commerce/identity/v1/user" in url:
            return self._identity()

        raise AssertionError(f"unexpected outbound eBay request: {url}")

    async def _token(self, request: httpx.Request) -> httpx.Response:
        body = dict(httpx.QueryParams(request.content.decode()))
        self.token_requests.append(body)
        grant = body.get("grant_type")

        if grant == "refresh_token":
            if self.second_refresh_seen is not None and self.refresh_count >= 2:
                self.second_refresh_seen.set()
            hold, self.hold_next_refresh = self.hold_next_refresh, None
            if hold is not None:
                if self.refresh_reached is not None:
                    self.refresh_reached.set()
                await asyncio.wait_for(hold.wait(), timeout=HANG_GUARD_SECONDS)

        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": self.token_error})

        payload: dict[str, Any] = {
            "access_token": (REFRESHED_ACCESS_TOKEN if grant == "refresh_token" else ACCESS_TOKEN),
            "expires_in": self.access_expires_in,
            "token_type": "User Access Token",
        }
        if self.granted_scope is not None:
            payload["refresh_token_scope"] = self.granted_scope
        if grant == "authorization_code" and self.issue_refresh_token:
            payload["refresh_token"] = REFRESH_TOKEN
            payload["refresh_token_expires_in"] = 47_304_000
        return httpx.Response(200, json=payload)

    def _identity(self) -> httpx.Response:
        self.identity_calls += 1
        if self.identity_status != 200:
            return httpx.Response(self.identity_status, json={"errors": []})
        return httpx.Response(
            200,
            json={
                "userId": self.seller_user_id,
                "username": self.username,
                "accountType": self.account_type,
                "registrationMarketplaceId": self.marketplace_id,
                "status": "CONFIRMED",
            },
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def install(monkeypatch: Any, fake: FakeEbay) -> None:
    """Point both eBay HTTP clients at the fake, and configure OAuth.

    Patched at ``httpx.AsyncClient`` inside each client module rather than at
    the module's own function, so the real timeout, header and error-handling
    code in ``tokens.py`` and ``identity.py`` still runs.
    """
    from app.integrations.ebay import identity as identity_module
    from app.integrations.ebay import tokens as tokens_module

    transport = fake.transport()
    real_client = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(tokens_module.httpx, "AsyncClient", patched)
    monkeypatch.setattr(identity_module.httpx, "AsyncClient", patched)

    monkeypatch.setattr(settings.ebay, "client_id", "DropPilo-TestOnly-PRD-000000")
    monkeypatch.setattr(settings.ebay, "client_secret", SecretStr("test-only-cert-id"))
    monkeypatch.setattr(settings.ebay, "redirect_uri_name", "DropPilot-TestOnly-RuName")
    monkeypatch.setattr(settings.ebay, "environment", EbayEnvironment.PRODUCTION)


SessionFactory = Callable[[], AsyncSession]


@asynccontextmanager
async def live_tenants(count: int = 1) -> AsyncIterator[tuple[list[uuid.UUID], Any]]:
    """Commit N tenants; yield their ids and a session factory. Drop them after.

    Cleanup is one delete per tenant, which cascades to the connection row.
    """
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    created: list[uuid.UUID] = []
    try:
        for index in range(count):
            suffix = uuid.uuid4().hex[:10]
            async with factory() as session:
                tenant = Tenant(
                    name=f"eBay C1 {index} {suffix}",
                    slug=f"ebay-c1-{suffix}",
                    status=TenantStatus.ACTIVE,
                )
                session.add(tenant)
                await session.flush()
                await session.commit()
                created.append(tenant.id)
        yield created, factory
    finally:
        async with factory() as cleanup:
            if created:
                await cleanup.execute(sa.delete(Tenant).where(Tenant.id.in_(created)))
                await cleanup.commit()
        await engine.dispose()
        clear_context()


@asynccontextmanager
async def own_connection(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A session on its own engine, with tenant context bound.

    A private engine, not another session on a shared one: the point is a
    separate PostgreSQL backend that can genuinely block on another's row lock.
    """
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            set_tenant_id(tenant_id)
            yield session
    finally:
        await engine.dispose()


async def connect_seller(
    factory: SessionFactory,
    tenant_id: uuid.UUID,
    *,
    code: str = "consent-code",
) -> uuid.UUID:
    """Run one full consent flow through the real service. Returns the row id."""
    from app.integrations.ebay.connection import EbayConnectionService

    async with factory() as session:
        set_tenant_id(tenant_id)
        service = EbayConnectionService(session)
        _, state = await service.begin_connection(user_id=None)
        connection = await service.complete_connection(code=code, state_token=state)
        await session.commit()
        return connection.id


async def expire_access_token(
    factory: SessionFactory, tenant_id: uuid.UUID, *, seconds: int = 5
) -> None:
    """Push the stored access token inside the refresh margin.

    Written straight to the column rather than waited for: the margin is 300
    seconds, and a test that slept through it would be a five-minute test.
    """
    async with factory() as session:
        await session.execute(
            sa.update(EbayConnection)
            .where(EbayConnection.tenant_id == tenant_id)
            .values(access_token_expires_at=datetime.now(UTC) + timedelta(seconds=seconds))
        )
        await session.commit()


async def load_connection(factory: SessionFactory, tenant_id: uuid.UUID) -> EbayConnection:
    """Read the row straight from the table, bypassing the service entirely."""
    async with factory() as session:
        return (
            await session.execute(
                sa.select(EbayConnection).where(EbayConnection.tenant_id == tenant_id)
            )
        ).scalar_one()


__all__ = [
    "ACCESS_TOKEN",
    "REFRESHED_ACCESS_TOKEN",
    "REFRESH_TOKEN",
    "SELLER_USERNAME",
    "SELLER_USER_ID",
    "FakeEbay",
    "SessionFactory",
    "connect_seller",
    "expire_access_token",
    "install",
    "live_tenants",
    "load_connection",
    "own_connection",
]
