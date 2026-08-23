"""GQL-2 F-01b — a webhook confirmation must describe the *current* state.

The first acceptance fix made a store that had *never* reconciled visibly
degraded. It did not touch the opposite case: a store that reconciled healthily
once and then went wrong.

`webhooks_registered_at` was only ever written, never cleared. So a later
unhealthy reconciliation, a provider outage, or a reconnect with brand-new OAuth
credentials all left the previous timestamp standing, and `webhook_health()`
read any non-null value as healthy. The card said *Webhooks active* for a store
whose subscriptions were missing — and because the Retry control only appears
while degraded, the stale timestamp also hid the way out.

The invariant these tests exist to enforce:

    A non-null `webhooks_registered_at` means the most recent **completed**
    reconciliation for the **current** connection credentials was healthy.

Not "some reconciliation once succeeded".

That requires the previous confirmation to be **committed as NULL before any
Shopify request is made**, so a crash, timeout or rolled-back request leaves the
store degraded rather than restoring a stale confirmation. Several tests below
therefore read the row from a *different* PostgreSQL connection while a
reconciliation is deliberately held mid-flight — an assertion about committed
state, not about what the in-flight session happens to hold.

Only Shopify's wire is faked. The service, the repositories, the row lock, the
transaction boundaries and the reconciler are production code.

No Shopify credential is used and no live request is made.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import set_tenant_id
from app.core.encryption import decrypt
from app.core.tokens import create_access_token
from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.graphql import ShopifyGraphQLClient
from app.integrations.shopify.service import ShopifyService
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
CALLBACK = "/api/v1/integrations/shopify/callback"


# --------------------------------------------------------------------- helpers
def headers_for(tenant_id: uuid.UUID, *, role: str = "admin") -> dict[str, str]:
    issued = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, roles=(role,))
    return {"Authorization": f"Bearer {issued.token}"}


@pytest.fixture
def shopify(monkeypatch: pytest.MonkeyPatch) -> CountingShopify:
    double = CountingShopify()
    monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", double.client)
    return double


@pytest.fixture
def shopify_app_credentials(monkeypatch: pytest.MonkeyPatch) -> str:
    """Placeholder app identifiers — they authenticate to nothing.

    Needed only so `complete_connection` reaches its persistence branch, which
    is the code under test. The HMAC below is computed with this same secret, so
    the real signature check runs rather than being stubbed away.
    """
    secret = "f01b-placeholder-app-secret-not-real"
    monkeypatch.setattr(settings.shopify, "api_key", "f01b-placeholder-app-key")
    from pydantic import SecretStr

    monkeypatch.setattr(settings.shopify, "api_secret", SecretStr(secret))
    return secret


async def http_client(session: AsyncSession) -> AsyncClient:
    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override() -> Any:
        yield session

    app.dependency_overrides[get_db_session] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def stamp(live: LiveStore) -> datetime | None:
    """Read the committed timestamp on a connection of its own.

    Deliberately not the session under test: the whole question is what another
    process would see, and a read through the in-flight session would answer a
    different one.
    """
    async with live.factory() as observer:
        return (
            await observer.execute(
                sa.select(ShopifyConnection.webhooks_registered_at).where(
                    ShopifyConnection.store_id == live.store_id
                )
            )
        ).scalar_one()


async def stored_token(live: LiveStore) -> str:
    async with live.factory() as observer:
        encrypted = (
            await observer.execute(
                sa.select(ShopifyConnection.encrypted_access_token).where(
                    ShopifyConnection.store_id == live.store_id
                )
            )
        ).scalar_one()
    return decrypt(encrypted)


async def make_healthy(live: LiveStore) -> datetime:
    """Reconcile successfully, leaving a real confirmation behind."""
    async with own_connection(live) as session:
        report = await ShopifyService(session).register_webhooks(live.store_id)
        await session.commit()
    assert report.healthy is True
    confirmed = await stamp(live)
    assert confirmed is not None, "precondition: the store must start confirmed"
    return confirmed


def wire(double: CountingShopify, behaviour: str) -> Any:
    """A client factory whose creates fail in a chosen way."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("operationName") == "WebhookSubscriptionCreate":
            double.creates.append(body["variables"])
            if behaviour == "timeout":
                raise httpx.ReadTimeout("shopify went quiet", request=request)
            if behaviour == "user_error":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "webhookSubscriptionCreate": {
                                "webhookSubscription": None,
                                "userErrors": [{"field": ["topic"], "message": "denied"}],
                            }
                        }
                    },
                )
        if behaviour == "dead":
            raise httpx.ConnectError("dns", request=request)
        return await double.handler(request)

    def client(*, shop_domain: str, access_token: str, **_: Any) -> ShopifyGraphQLClient:
        return ShopifyGraphQLClient(
            shop_domain=shop_domain,
            access_token=access_token,
            max_attempts=1,
            transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    return client


async def retry_via_http(live: LiveStore, *, role: str = "admin") -> httpx.Response:
    async with own_connection(live) as session:
        client = await http_client(session)
        async with client:
            response = await client.post(
                RECONCILE.format(store_id=live.store_id),
                headers=headers_for(live.tenant_id, role=role),
            )
        if response.status_code < 400:
            await session.commit()
        else:
            await session.rollback()
    return response


# ------------------------------------------------- a previously healthy store
class TestConfirmationDoesNotSurviveAFailure:
    """The core of F-01b: one good run must not vouch for every later one."""

    async def test_a_later_unhealthy_report_clears_the_confirmation(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)

            # Shopify starts refusing every create. The subscriptions listed
            # already exist, so this run is unhealthy only for the topics it
            # cannot confirm -- exactly the partial failure a stale timestamp
            # used to hide.
            shopify.subscriptions.clear()
            monkeypatch.setattr(
                shopify_service_module, "ShopifyGraphQLClient", wire(shopify, "user_error")
            )
            response = await retry_via_http(live)

            assert response.status_code == 200, response.text
            assert response.json()["healthy"] is False
            assert response.json()["webhookHealth"] == "degraded"
            assert await stamp(live) is None, (
                "a confirmation from an earlier run must not survive a failed one"
            )

    async def test_a_provider_exception_clears_the_confirmation(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)

            monkeypatch.setattr(
                shopify_service_module, "ShopifyGraphQLClient", wire(shopify, "dead")
            )
            response = await retry_via_http(live)

            assert response.status_code >= 400
            assert await stamp(live) is None, (
                "the request rolled back, so an uncommitted clear would have "
                "restored the stale confirmation"
            )

    async def test_an_unknown_mutation_outcome_clears_the_confirmation(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)

            shopify.subscriptions.clear()
            monkeypatch.setattr(
                shopify_service_module, "ShopifyGraphQLClient", wire(shopify, "timeout")
            )
            response = await retry_via_http(live)

            body = response.json()
            assert body["healthy"] is False
            assert all(topic["status"] == "unknown" for topic in body["topics"])
            assert await stamp(live) is None

    async def test_a_later_healthy_run_writes_a_new_confirmation(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recovery still works — invalidation is not a one-way door."""
        async with live_stores() as (live,):
            first = await make_healthy(live)

            shopify.subscriptions.clear()
            monkeypatch.setattr(
                shopify_service_module, "ShopifyGraphQLClient", wire(shopify, "timeout")
            )
            await retry_via_http(live)
            assert await stamp(live) is None

            monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", shopify.client)
            response = await retry_via_http(live)

            assert response.json()["healthy"] is True
            second = await stamp(live)
            assert second is not None
            assert second > first, "a recovery must record when it actually happened"


class TestInvalidationIsCommittedBeforeProviderWork:
    """Crash-safety: the clear must outlive the request that made it."""

    async def test_the_confirmation_is_already_gone_while_shopify_is_being_called(
        self, shopify: CountingShopify
    ) -> None:
        """Read from a different connection, mid-flight.

        This is the assertion that distinguishes a committed invalidation from a
        pending one. If the clear were merely flushed, this observer — on its
        own PostgreSQL connection — would still see the old timestamp.
        """
        async with live_stores() as (live,):
            await make_healthy(live)

            release = asyncio.Event()
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            retry = asyncio.create_task(retry_via_http(live))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)

            # Shopify has been called; the request has not finished.
            assert await stamp(live) is None, (
                "another process could still read the store as confirmed while "
                "its reconciliation was in flight"
            )

            release.set()
            response = await asyncio.wait_for(retry, timeout=HANG_GUARD_SECONDS)
            assert response.status_code == 200
            assert await stamp(live) is not None

    async def test_a_status_read_during_reconciliation_reports_degraded(
        self, shopify: CountingShopify
    ) -> None:
        """Through the real API, not just the column."""
        async with live_stores() as (live,):
            await make_healthy(live)

            release = asyncio.Event()
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            retry = asyncio.create_task(retry_via_http(live))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)

            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    status = await client.get(STATUS, headers=headers_for(live.tenant_id))

            row = status.json()["connections"][0]
            assert row["webhookHealth"] == "degraded"
            assert row["webhooksRegisteredAt"] is None

            release.set()
            await asyncio.wait_for(retry, timeout=HANG_GUARD_SECONDS)


class TestReadsNeverMutate:
    async def test_viewing_the_status_page_does_not_clear_the_confirmation(
        self, shopify: CountingShopify
    ) -> None:
        """Invalidation belongs to reconciliation, not to looking at it."""
        async with live_stores() as (live,):
            confirmed = await make_healthy(live)

            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    for _ in range(3):
                        response = await client.get(STATUS, headers=headers_for(live.tenant_id))
                        assert response.json()["connections"][0]["webhookHealth"] == "healthy"

            assert await stamp(live) == confirmed
            assert shopify.lists == 1, "a read must not reconcile"


# --------------------------------------------------------------------- reconnect
def signed_callback(*, secret: str, shop_domain: str, state: str) -> str:
    """A callback query string carrying a genuinely valid HMAC."""
    pairs = [("code", "f01b-code"), ("shop", shop_domain), ("state", state)]
    message = "&".join(f"{k}={v}" for k, v in sorted(pairs))
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return "&".join(f"{k}={v}" for k, v in pairs) + f"&hmac={digest}"


class TestReconnect:
    """New credentials must never inherit the old credentials' confirmation."""

    @pytest.fixture(autouse=True)
    def _oauth_stubs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        shopify_app_credentials: str,
    ) -> None:
        # Only the two genuinely external steps are replaced: Redis state and
        # the token exchange. HMAC verification, ownership checks and every
        # line of the persistence branch run for real.
        async def fake_consume(self: ShopifyService, state: str) -> dict[str, Any]:
            return json.loads(state)

        async def fake_exchange(**kwargs: Any) -> dict[str, Any]:
            return {"access_token": "shpat_f01b_rotated_not_real", "scope": "read_products"}

        monkeypatch.setattr(ShopifyService, "_consume_state", fake_consume)
        monkeypatch.setattr(ShopifyClient, "exchange_token", staticmethod(fake_exchange))

    async def reconnect(self, live: LiveStore, secret: str) -> httpx.Response:
        state = json.dumps({"tenant_id": str(live.tenant_id), "shop_domain": live.shop_domain})
        query = signed_callback(secret=secret, shop_domain=live.shop_domain, state=state)
        async with own_connection(live) as session:
            client = await http_client(session)
            async with client:
                response = await client.get(f"{CALLBACK}?{query}", follow_redirects=False)
            await session.commit()
        return response

    async def test_a_reconnect_with_new_credentials_clears_the_old_confirmation(
        self, shopify: CountingShopify, shopify_app_credentials: str
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)
            before_token = await stored_token(live)

            shopify.subscriptions.clear()
            response = await self.reconnect(live, shopify_app_credentials)

            assert response.status_code == 303, response.text
            after_token = await stored_token(live)
            assert after_token != before_token, "precondition: credentials were rotated"
            # This run reconciled healthily, so a *new* confirmation is correct
            # -- what must not happen is the old one being carried over.
            assert await stamp(live) is not None
            assert shopify.creates, "the reconnect must have re-registered the topics"

    async def test_a_reconnect_whose_reconciliation_fails_stays_degraded(
        self,
        shopify: CountingShopify,
        shopify_app_credentials: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)

            shopify.subscriptions.clear()
            monkeypatch.setattr(
                shopify_service_module, "ShopifyGraphQLClient", wire(shopify, "timeout")
            )
            response = await self.reconnect(live, shopify_app_credentials)

            assert response.status_code == 303
            flag = parse_qs(urlsplit(str(response.headers["location"])).query).get("shopify", [])
            assert flag == ["connected_webhooks_degraded"], (
                "an old timestamp must not let a failed reconnect look successful"
            )
            assert await stamp(live) is None
            # The token is still the refreshed one: OAuth is preserved.
            assert await stored_token(live) == "shpat_f01b_rotated_not_real"

    async def test_a_reconnect_whose_reconciliation_throws_stays_degraded(
        self,
        shopify: CountingShopify,
        shopify_app_credentials: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)

            monkeypatch.setattr(
                shopify_service_module, "ShopifyGraphQLClient", wire(shopify, "dead")
            )
            response = await self.reconnect(live, shopify_app_credentials)

            assert response.status_code == 303
            flag = parse_qs(urlsplit(str(response.headers["location"])).query).get("shopify", [])
            assert flag == ["connected_webhooks_degraded"]
            assert await stamp(live) is None

    async def test_a_successful_reconnect_records_a_fresh_confirmation(
        self, shopify: CountingShopify, shopify_app_credentials: str
    ) -> None:
        async with live_stores() as (live,):
            first = await make_healthy(live)
            shopify.subscriptions.clear()

            response = await self.reconnect(live, shopify_app_credentials)

            assert response.status_code == 303
            flag = parse_qs(urlsplit(str(response.headers["location"])).query).get("shopify", [])
            assert flag == ["connected"]
            second = await stamp(live)
            assert second is not None and second > first


# ------------------------------------------------------- concurrency & locking
class TestConcurrencyStillHolds:
    async def test_concurrent_retries_still_create_at_most_one_per_topic(
        self, shopify: CountingShopify
    ) -> None:
        """Invalidation must not have widened the duplicate window."""
        release = asyncio.Event()
        async with live_stores() as (live,):
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            first = asyncio.create_task(retry_via_http(live))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)
            second = asyncio.create_task(retry_via_http(live))
            await asyncio.sleep(0.2)
            release.set()

            first_response = await asyncio.wait_for(first, timeout=HANG_GUARD_SECONDS)
            second_response = await asyncio.wait_for(second, timeout=HANG_GUARD_SECONDS)

        assert first_response.status_code == 200
        assert second_response.status_code in (200, 409), second_response.status_code
        for topic in {call["topic"] for call in shopify.creates}:
            assert len(shopify.creates_for(topic)) == 1, f"{topic} was created twice"

    async def test_a_busy_store_does_not_restore_the_stale_confirmation(
        self, shopify: CountingShopify
    ) -> None:
        """The loser is told 409 — and the store is left degraded, not healthy.

        The first retry has already committed the invalidation and is holding
        the connection row while it talks to Shopify. The second cannot take
        that row, times out, and its request rolls back. Nothing in that
        rollback may bring the old confirmation back.
        """
        release = asyncio.Event()
        async with live_stores() as (live,):
            await make_healthy(live)
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            first = asyncio.create_task(retry_via_http(live))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)
            assert await stamp(live) is None

            second_response = await asyncio.wait_for(
                retry_via_http(live), timeout=HANG_GUARD_SECONDS
            )
            assert second_response.status_code == 409, second_response.text
            assert second_response.json()["code"] == "shopify_webhook_reconcile_busy"
            assert await stamp(live) is None, "a rejected retry restored a stale confirmation"

            release.set()
            assert (await asyncio.wait_for(first, timeout=HANG_GUARD_SECONDS)).status_code == 200

    async def test_two_stores_remain_independent(self, shopify: CountingShopify) -> None:
        release = asyncio.Event()
        async with live_stores(2) as (held, free):
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            blocked = asyncio.create_task(retry_via_http(held))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)

            other = await asyncio.wait_for(retry_via_http(free), timeout=HANG_GUARD_SECONDS)
            release.set()
            assert (await asyncio.wait_for(blocked, timeout=HANG_GUARD_SECONDS)).status_code == 200

        assert other.status_code == 200, other.text
        assert await_free_is_healthy(other)


def await_free_is_healthy(response: httpx.Response) -> bool:
    return bool(response.json()["healthy"])


class TestAuthorisationUnchanged:
    async def test_a_viewer_cannot_invalidate_a_confirmation(
        self, shopify: CountingShopify
    ) -> None:
        """403 must land before anything is cleared."""
        async with live_stores() as (live,):
            confirmed = await make_healthy(live)

            response = await retry_via_http(live, role="viewer")

            assert response.status_code == 403
            assert await stamp(live) == confirmed, (
                "a refused caller must not be able to degrade a healthy store"
            )

    async def test_a_foreign_store_is_neither_cleared_nor_enumerated(
        self, shopify: CountingShopify
    ) -> None:
        async with live_stores(2) as (owner, other):
            confirmed = await make_healthy(owner)

            async with own_connection(other) as session:
                set_tenant_id(other.tenant_id)
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
                await session.rollback()

            assert foreign.status_code == unknown.status_code
            assert foreign.json()["code"] == unknown.json()["code"]
            assert foreign.status_code != 403
            assert await stamp(owner) == confirmed, (
                "another tenant must not be able to degrade this store"
            )


class TestNoSecretsLeak:
    async def test_no_token_appears_in_a_reconcile_or_status_response(
        self, shopify: CountingShopify
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)
            response = await retry_via_http(live)
            async with own_connection(live) as session:
                client = await http_client(session)
                async with client:
                    status = await client.get(STATUS, headers=headers_for(live.tenant_id))

        for payload in (response.text, status.text):
            assert "shpat_" not in payload
            assert "encrypted_access_token" not in payload
            assert "accessToken" not in payload


class TestOutcomeMatrix:
    """The table from the brief, asserted as one parameterised statement."""

    @pytest.mark.parametrize(
        ("behaviour", "expect_stamp"),
        [
            ("ok", True),
            ("user_error", False),
            ("timeout", False),
            ("dead", False),
        ],
    )
    async def test_the_stamp_matches_the_outcome(
        self,
        shopify: CountingShopify,
        monkeypatch: pytest.MonkeyPatch,
        behaviour: str,
        expect_stamp: bool,
    ) -> None:
        async with live_stores() as (live,):
            await make_healthy(live)
            shopify.subscriptions.clear()
            if behaviour != "ok":
                monkeypatch.setattr(
                    shopify_service_module, "ShopifyGraphQLClient", wire(shopify, behaviour)
                )
            await retry_via_http(live)
            assert (await stamp(live) is not None) is expect_stamp
        _ = datetime.now(UTC)
