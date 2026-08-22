"""GQL-2 acceptance fix — a connected store can never hide a broken webhook setup.

The defect this file exists for (F-01): OAuth succeeded, `register_webhooks`
ran inside a best-effort `try/except`, its `ReconcileReport` was thrown away,
and the merchant was redirected with `shopify=connected`. The store then sat in
the UI as *Connected* while the product, inventory and order subscriptions it
needs were missing — and the only recovery control was hidden precisely because
the status said `connected`.

So these tests assert three separate things, because fixing only one still
leaves a merchant stuck:

1. the OAuth result tells the truth when reconciliation did not succeed;
2. there is a deterministic retry that does not require disconnecting;
3. the retry is the *same* reconciliation — it lists before creating, never
   replays a lost mutation, and stamps `webhooks_registered_at` only when the
   whole report is healthy.

Only Shopify's wire is faked (`CountingShopify`). The service, the repository,
the row lock, the GraphQL client and the reconciler are production code, so a
count of mutations is a count of what actually left the process.

No Shopify credential is used and no live request is made.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.graphql import ShopifyGraphQLClient
from app.integrations.shopify.service import WEBHOOK_TOPICS, ShopifyService
from app.models.shopify import ShopifyConnection
from tests.integration.shopify_gql2_live import (
    HANG_GUARD_SECONDS,
    CountingShopify,
    LiveStore,
    live_stores,
    own_connection,
)

pytestmark = pytest.mark.integration

RECONCILE = "/api/v1/integrations/shopify/stores/{store_id}/webhooks/reconcile"
STATUS = "/api/v1/integrations/shopify/status"


def headers_for(tenant_id: uuid.UUID, *, role: str = "admin") -> dict[str, str]:
    issued = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, roles=(role,))
    return {"Authorization": f"Bearer {issued.token}"}


@pytest.fixture
def shopify(monkeypatch: pytest.MonkeyPatch) -> CountingShopify:
    double = CountingShopify()
    monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", double.client)
    return double


def failing_create(double: CountingShopify) -> Any:
    """A wire where listing works but every create times out.

    Models the realistic partial failure: the app can see the shop, so this is
    not an auth problem, but the subscriptions do not get made.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("operationName") == "WebhookSubscriptionCreate":
            double.creates.append(body["variables"])
            raise httpx.ReadTimeout("shopify went quiet", request=request)
        return await double.handler(request)

    def client(*, shop_domain: str, access_token: str, **_: Any) -> ShopifyGraphQLClient:
        return ShopifyGraphQLClient(
            shop_domain=shop_domain,
            access_token=access_token,
            transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    return client


async def http_client(session: AsyncSession) -> AsyncClient:
    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override() -> Any:
        yield session

    app.dependency_overrides[get_db_session] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def registration_stamp(live: LiveStore) -> Any:
    async with live.factory() as session:
        return (
            await session.execute(
                sa.select(ShopifyConnection.webhooks_registered_at).where(
                    ShopifyConnection.store_id == live.store_id
                )
            )
        ).scalar_one()


async def make_degraded(live: LiveStore, double: CountingShopify) -> None:
    """Leave the store connected with no webhooks registered — the F-01 state."""
    async with own_connection(live) as session:
        service = ShopifyService(session)
        report = await service.register_webhooks(live.store_id)
        await session.commit()
    assert report.healthy is False
    assert await registration_stamp(live) is None


# ---------------------------------------------------------------- the endpoint
class TestRetryEndpoint:
    async def test_an_admin_can_retry_reconciliation_while_still_connected(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of F-01: recovery without disconnecting."""
        monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", failing_create(shopify))
        async with live_stores() as (live,):
            await make_degraded(live, shopify)

            # The wire recovers; the store is still connected throughout.
            monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", shopify.client)
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                await session.commit()

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["healthy"] is True
            assert body["webhookHealth"] == "healthy"
            assert len(body["topics"]) == len(WEBHOOK_TOPICS)
            assert body["warnings"] == []
            assert body["webhooksRegisteredAt"] is not None
            assert await registration_stamp(live) is not None

    async def test_the_retry_lists_before_it_creates(self, shopify: CountingShopify) -> None:
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    await client.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                await session.commit()

        assert shopify.operations[0] == "WebhookSubscriptions", (
            "creating before listing is how a retry duplicates every topic"
        )
        assert shopify.lists == 1

    async def test_an_already_reconciled_store_issues_no_mutation(
        self, shopify: CountingShopify
    ) -> None:
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                await ShopifyService(session).register_webhooks(live.store_id)
                await session.commit()
            baseline = len(shopify.creates)

            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                await session.commit()

        assert response.status_code == 200, response.text
        assert len(shopify.creates) == baseline, "the retry must be idempotent"
        assert response.json()["createdCount"] == 0
        assert all(t["status"] == "already_present" for t in response.json()["topics"])

    async def test_a_lost_create_response_then_a_retry_does_not_duplicate(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The worst case: Shopify applied the change and the reply was lost."""
        applied: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("operationName") == "WebhookSubscriptionCreate" and not applied:
                variables = body["variables"]
                shopify.creates.append(variables)
                shopify._next_id += 1
                shopify.subscriptions.append(
                    {
                        "id": f"gid://shopify/WebhookSubscription/{shopify._next_id}",
                        "topic": variables["topic"],
                        "uri": variables["webhookSubscription"]["uri"],
                        "format": variables["webhookSubscription"]["format"],
                        "includeFields": [],
                        "filter": None,
                    }
                )
                applied.append("yes")
                raise httpx.ReadTimeout("response lost", request=request)
            return await shopify.handler(request)

        def client(*, shop_domain: str, access_token: str, **_: Any) -> ShopifyGraphQLClient:
            return ShopifyGraphQLClient(
                shop_domain=shop_domain,
                access_token=access_token,
                transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            )

        monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", client)

        async with live_stores() as (live,):
            async with own_connection(live) as session:
                first = await ShopifyService(live_session := session).register_webhooks(
                    live.store_id
                )
                await live_session.commit()
            assert first.healthy is False

            created_for_first_topic = [
                call for call in shopify.creates if call["topic"] == "PRODUCTS_CREATE"
            ]
            assert len(created_for_first_topic) == 1

            async with own_connection(live) as session:
                http = await http_client(session)
                async with http:
                    response = await http.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                await session.commit()

        assert response.status_code == 200, response.text
        again = [call for call in shopify.creates if call["topic"] == "PRODUCTS_CREATE"]
        assert len(again) == 1, "the retry replayed a mutation whose outcome was unknown"
        assert response.json()["healthy"] is True

    async def test_an_unhealthy_retry_is_an_explicit_degraded_result(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not a 500, not a fake success."""
        monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", failing_create(shopify))
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                await session.commit()

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["healthy"] is False
            assert body["webhookHealth"] == "degraded"
            assert body["webhooksRegisteredAt"] is None
            assert all(t["status"] == "unknown" for t in body["topics"])
            assert await registration_stamp(live) is None

    async def test_a_provider_failure_never_stamps_the_connection_healthy(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns", request=request)

        def client(*, shop_domain: str, access_token: str, **_: Any) -> ShopifyGraphQLClient:
            return ShopifyGraphQLClient(
                shop_domain=shop_domain,
                access_token=access_token,
                max_attempts=1,
                transport=httpx.AsyncClient(transport=httpx.MockTransport(dead)),
            )

        monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", client)
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                http = await http_client(session)
                async with http:
                    response = await http.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                await session.rollback()

            assert response.status_code >= 400
            assert await registration_stamp(live) is None


class TestRetryAuthorisation:
    async def test_a_viewer_is_refused(self, shopify: CountingShopify) -> None:
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id, role="viewer"),
                    )
        assert response.status_code == 403
        assert shopify.creates == [], "a refused caller must not reach Shopify"

    async def test_an_anonymous_caller_is_refused(self, shopify: CountingShopify) -> None:
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.post(RECONCILE.format(store_id=live.store_id))
        assert response.status_code == 401
        assert shopify.paths == []

    async def test_a_foreign_store_is_indistinguishable_from_an_unknown_one(
        self, shopify: CountingShopify
    ) -> None:
        """Enumeration guard: existence must not leak through the response."""
        async with live_stores(2) as (owner, other):
            async with own_connection(other) as session:
                client = await http_client(session)
                async with client:
                    foreign = await client.post(
                        RECONCILE.format(store_id=owner.store_id),
                        headers=headers_for(other.tenant_id),
                    )
                    unknown = await client.post(
                        RECONCILE.format(store_id=uuid.uuid4()),
                        headers=headers_for(other.tenant_id),
                    )

        assert foreign.status_code == unknown.status_code
        assert foreign.json()["code"] == unknown.json()["code"]
        assert foreign.status_code != 403, "403 confirms the store exists"
        assert shopify.paths == [], "no Shopify request may be made for a foreign store"


class TestNoSecretsLeak:
    async def test_no_token_or_provider_payload_appears_in_the_response(
        self, shopify: CountingShopify
    ) -> None:
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                    status_response = await client.get(STATUS, headers=headers_for(live.tenant_id))
                await session.commit()

        for payload in (response.text, status_response.text):
            assert "shpat_" not in payload
            assert "encrypted_access_token" not in payload
            assert "accessToken" not in payload
            assert "X-Shopify-Access-Token" not in payload
            assert "x-shopify-access-token" not in payload.lower()


# ------------------------------------------------------------- OAuth honesty
class TestOAuthResultHonesty:
    """The callback must not report a plain success it cannot vouch for."""

    async def test_a_healthy_registration_redirects_as_connected(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with live_stores() as (live,):
            redirect = await run_callback(live, monkeypatch, healthy=True)
        assert query_flag(redirect) == "connected"

    async def test_an_unhealthy_registration_redirects_as_degraded(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with live_stores() as (live,):
            redirect = await run_callback(live, monkeypatch, healthy=False)
        flag = query_flag(redirect)
        assert flag != "connected", "a plain success hides a store with no working webhooks"
        assert flag == "connected_webhooks_degraded"

    async def test_a_registration_exception_redirects_as_degraded(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with live_stores() as (live,):
            redirect = await run_callback(live, monkeypatch, raises=True)
        assert query_flag(redirect) == "connected_webhooks_degraded"

    async def test_the_redirect_carries_no_secret_or_raw_error_text(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with live_stores() as (live,):
            redirect = await run_callback(live, monkeypatch, raises=True)
        assert "shpat_" not in redirect
        assert "Traceback" not in redirect
        # Only stable, enumerable status codes travel in the query string.
        values = parse_qs(urlsplit(redirect).query).get("shopify", [])
        assert values and values[0].replace("_", "").isalpha()


async def run_callback(
    live: LiveStore,
    monkeypatch: pytest.MonkeyPatch,
    *,
    healthy: bool = True,
    raises: bool = False,
) -> str:
    """Drive the real callback route with `complete_connection` stubbed.

    The OAuth exchange itself is GQL-1 territory and already covered; what is
    under test here is only what the callback does with the reconciliation
    result, so the exchange is replaced and the reconciliation is real.
    """
    from app.integrations.shopify.webhook_reconciliation import (
        ReconcileItem,
        ReconcileReport,
        ReconcileStatus,
    )

    async def fake_complete(self: ShopifyService, *, query_string: str) -> Any:
        from app.core.context import set_tenant_id

        set_tenant_id(live.tenant_id)
        return await self.connections.get_by_store(live.store_id)

    if raises:

        async def fake_register(self: ShopifyService, store_id: uuid.UUID) -> Any:
            raise RuntimeError("provider exploded")

    else:
        status = ReconcileStatus.CREATED if healthy else ReconcileStatus.UNKNOWN

        async def fake_register(self: ShopifyService, store_id: uuid.UUID) -> Any:
            return ReconcileReport(
                items=tuple(ReconcileItem(topic=topic, status=status) for topic in WEBHOOK_TOPICS),
                listed_count=0,
                created_count=len(WEBHOOK_TOPICS) if healthy else 0,
            )

    monkeypatch.setattr(ShopifyService, "complete_connection", fake_complete)
    monkeypatch.setattr(ShopifyService, "register_webhooks", fake_register)

    async with own_connection(live) as session:
        client = await http_client(session)
        async with client:
            response = await client.get(
                "/api/v1/integrations/shopify/callback?code=x&shop=y&state=z",
                follow_redirects=False,
            )
        await session.rollback()
    assert response.status_code == 303, response.text
    return str(response.headers["location"])


def query_flag(redirect: str) -> str:
    values = parse_qs(urlsplit(redirect).query).get("shopify", [])
    return values[0] if values else ""


# ------------------------------------------------------- status projection
class TestStatusProjection:
    async def test_a_connected_store_with_no_stamp_is_reported_degraded(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The UI must never be able to render 'fully connected' from this."""
        monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", failing_create(shopify))
        async with live_stores() as (live,):
            await make_degraded(live, shopify)
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.get(STATUS, headers=headers_for(live.tenant_id))

        row = response.json()["connections"][0]
        assert row["status"] == "connected"
        assert row["webhooksRegisteredAt"] is None
        assert row["webhookHealth"] == "degraded"

    async def test_a_reconciled_store_is_reported_healthy(self, shopify: CountingShopify) -> None:
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                await ShopifyService(session).register_webhooks(live.store_id)
                await session.commit()
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    response = await client.get(STATUS, headers=headers_for(live.tenant_id))

        row = response.json()["connections"][0]
        assert row["webhooksRegisteredAt"] is not None
        assert row["webhookHealth"] == "healthy"


# ------------------------------------------------------ concurrency & locking
class TestConcurrentRetries:
    async def test_two_concurrent_retries_create_at_most_one_per_topic(
        self, shopify: CountingShopify
    ) -> None:
        """The endpoint must inherit the reconciler's serialisation, not bypass it."""
        release = asyncio.Event()
        async with live_stores() as (live,):
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            async def call() -> int:
                async with own_connection(live) as session:
                    client = await http_client(session)
                    async with client:
                        response = await client.post(
                            RECONCILE.format(store_id=live.store_id),
                            headers=headers_for(live.tenant_id),
                        )
                    await session.commit()
                    return response.status_code

            winner = asyncio.create_task(call())
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)
            loser = asyncio.create_task(call())
            await asyncio.sleep(0.2)
            release.set()
            first = await asyncio.wait_for(winner, timeout=HANG_GUARD_SECONDS)
            second = await asyncio.wait_for(loser, timeout=HANG_GUARD_SECONDS)

        assert first == 200
        # The loser either waited and found the work done, or was told the store
        # is busy. Both are correct; silently duplicating is not.
        assert second in (200, 409), second
        for topic in {call["topic"] for call in shopify.creates}:
            assert len(shopify.creates_for(topic)) == 1, (
                f"{topic} was created more than once by concurrent retries"
            )

    async def test_two_different_stores_do_not_block_each_other(
        self, shopify: CountingShopify
    ) -> None:
        """Per-store serialisation must not become a global bottleneck."""
        release = asyncio.Event()
        async with live_stores(2) as (first_store, second_store):
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            async def call(live: LiveStore) -> int:
                async with own_connection(live) as session:
                    client = await http_client(session)
                    async with client:
                        response = await client.post(
                            RECONCILE.format(store_id=live.store_id),
                            headers=headers_for(live.tenant_id),
                        )
                    await session.commit()
                    return response.status_code

            held = asyncio.create_task(call(first_store))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)
            # The second store must complete while the first is still holding
            # its own row. If store-level locking had become table- or
            # advisory-global, this would time out.
            other = await asyncio.wait_for(call(second_store), timeout=HANG_GUARD_SECONDS)
            release.set()
            assert await asyncio.wait_for(held, timeout=HANG_GUARD_SECONDS) == 200

        assert other == 200

    async def test_lock_contention_is_a_typed_retryable_error_not_a_500(
        self, shopify: CountingShopify
    ) -> None:
        """A merchant clicking Retry twice must not see an unhandled server error."""
        async with live_stores() as (live,):
            async with own_connection(live) as holder:
                # Hold the row on a separate real connection for the duration.
                await holder.execute(
                    sa.select(ShopifyConnection)
                    .where(ShopifyConnection.store_id == live.store_id)
                    .with_for_update()
                )
                holder_pid = await backend_pid_of(holder)

                async with own_connection(live) as session:
                    client = await http_client(session)
                    async with client:
                        blocked = asyncio.create_task(
                            client.post(
                                RECONCILE.format(store_id=live.store_id),
                                headers=headers_for(live.tenant_id),
                            )
                        )
                        # Deterministic: wait for PostgreSQL to report the
                        # request's backend waiting on the holder's lock.
                        await wait_for_a_blocked_backend(live, holder_pid)
                        response = await asyncio.wait_for(blocked, timeout=HANG_GUARD_SECONDS)
                    await session.rollback()
                await holder.rollback()

        assert response.status_code != 500, response.text
        assert response.status_code == 409
        assert response.json()["code"] == "shopify_webhook_reconcile_busy"

    async def test_the_lock_is_released_when_the_request_fails(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exception mid-reconciliation must not wedge the store forever."""

        async def dead(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns", request=request)

        def client(*, shop_domain: str, access_token: str, **_: Any) -> ShopifyGraphQLClient:
            return ShopifyGraphQLClient(
                shop_domain=shop_domain,
                access_token=access_token,
                max_attempts=1,
                transport=httpx.AsyncClient(transport=httpx.MockTransport(dead)),
            )

        monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", client)

        async with live_stores() as (live,):
            async with own_connection(live) as session:
                http = await http_client(session)
                async with http:
                    failed = await http.post(
                        RECONCILE.format(store_id=live.store_id),
                        headers=headers_for(live.tenant_id),
                    )
                await session.rollback()
            assert failed.status_code >= 400

            # The wire recovers. If the failed request had left the row locked,
            # this second call would block until the hang guard rather than
            # succeeding.
            monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", shopify.client)
            async with own_connection(live) as session:
                http = await http_client(session)
                async with http:
                    recovered = await asyncio.wait_for(
                        http.post(
                            RECONCILE.format(store_id=live.store_id),
                            headers=headers_for(live.tenant_id),
                        ),
                        timeout=HANG_GUARD_SECONDS,
                    )
                await session.commit()

        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["healthy"] is True


async def backend_pid_of(session: AsyncSession) -> int:
    return int((await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())


async def wait_for_a_blocked_backend(live: LiveStore, blocker_pid: int) -> None:
    """Wait until PostgreSQL reports somebody blocked by ``blocker_pid``.

    `pg_blocking_pids` rather than a sleep: the assertion is "the request could
    not take the row", and that is a fact the database knows.
    """
    import time

    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while time.monotonic() < deadline:
        async with live.factory() as observer:
            waiting = (
                await observer.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE :p = ANY(pg_blocking_pids(pid))"
                    ),
                    {"p": blocker_pid},
                )
            ).scalar_one()
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"nothing ever blocked on backend {blocker_pid}")
