"""GQL-2 — two concurrent reconciliations may create at most one subscription.

The failure this exists to prevent is not hypothetical: OAuth completion, a
reconnect and the recovery path all call ``register_webhooks``, and two of them
overlapping would each list an empty set, each decide the topic was missing, and
each create it. The shop would then receive every product, inventory and order
event twice, and nothing in the application would notice.

Two real PostgreSQL connections, real contention, only Shopify's wire faked —
see ``tests/integration/shopify_gql2_live`` for the harness and why it is shaped
that way.

The last test in ``TestConcurrentReconciliation`` is the control: the same race
with the row lock removed *does* duplicate. Without it, a green concurrency test
could just mean the two reconcilers never actually overlapped.

No Shopify credential is used and no live request is made.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.exceptions import ShopifyNotConnectedError
from app.integrations.shopify.graphql import ShopifyGraphQLClient
from app.integrations.shopify.service import WEBHOOK_TOPICS, ShopifyService
from app.integrations.shopify.webhook_reconciliation import (
    ReconcileReport,
    ReconcileStatus,
    WebhookReconciler,
    desired_subscriptions,
)
from app.models.shopify import ShopifyConnection
from tests.integration.shopify_gql2_live import (
    HANG_GUARD_SECONDS,
    CountingShopify,
    LiveStore,
    live_stores,
    own_connection,
    wait_until_blocked,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def shopify(monkeypatch: pytest.MonkeyPatch) -> CountingShopify:
    """Replace only the client constructor the service reaches for."""
    double = CountingShopify()
    monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", double.client)
    return double


async def reconcile_on_own_connection(
    live: LiveStore,
    *,
    ready: asyncio.Event | None = None,
    pid_out: list[int] | None = None,
) -> ReconcileReport:
    """Run the production ``register_webhooks`` on a private connection."""
    from tests.integration.shopify_gql2_live import backend_pid

    async with own_connection(live) as session:
        if pid_out is not None:
            pid_out.append(await backend_pid(session))
        if ready is not None:
            ready.set()
        report = await ShopifyService(session).register_webhooks(live.store_id)
        await session.commit()
        return report


def enum_topics() -> list[str]:
    return [want.enum_topic for want in desired_subscriptions(WEBHOOK_TOPICS)]


class TestConcurrentReconciliation:
    async def test_two_concurrent_reconciliations_create_at_most_one_subscription(
        self, shopify: CountingShopify
    ) -> None:
        """The central invariant of GQL-2.

        Winner and loser are made deterministic by holding the winner inside its
        listing call — after it has taken the connection row — and releasing it
        only once PostgreSQL confirms the loser is blocked on that same row.
        """
        release = asyncio.Event()
        async with live_stores() as (live,):
            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()

            winner = asyncio.create_task(reconcile_on_own_connection(live))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)

            loser_ready = asyncio.Event()
            loser_pid: list[int] = []
            loser = asyncio.create_task(
                reconcile_on_own_connection(live, ready=loser_ready, pid_out=loser_pid)
            )
            await asyncio.wait_for(loser_ready.wait(), timeout=HANG_GUARD_SECONDS)

            blockers = await wait_until_blocked(live.factory, loser_pid[0])
            assert blockers, "the second reconciler was never blocked by the first"

            release.set()
            first = await asyncio.wait_for(winner, timeout=HANG_GUARD_SECONDS)
            second = await asyncio.wait_for(loser, timeout=HANG_GUARD_SECONDS)

        expected = len(WEBHOOK_TOPICS)
        assert len(shopify.creates) == expected, (
            f"expected one create per topic, got {len(shopify.creates)} — "
            "a duplicate delivers every event twice"
        )
        for enum_member in enum_topics():
            assert len(shopify.creates_for(enum_member)) == 1

        assert first.created_count == expected
        assert second.created_count == 0
        assert all(item.status is ReconcileStatus.ALREADY_PRESENT for item in second.items)
        assert shopify.lists == 2, "each reconciliation lists exactly once"

    async def test_a_sequential_second_run_repeats_nothing(self, shopify: CountingShopify) -> None:
        """Idempotence across separate connections, without any contention."""
        async with live_stores() as (live,):
            first = await reconcile_on_own_connection(live)
            second = await reconcile_on_own_connection(live)

        assert first.created_count == len(WEBHOOK_TOPICS)
        assert second.created_count == 0
        assert len(shopify.creates) == len(WEBHOOK_TOPICS)
        assert second.healthy is True

    async def test_the_same_race_without_the_row_lock_does_duplicate(
        self, shopify: CountingShopify
    ) -> None:
        """The control that makes the test above mean something.

        Two reconcilers driven directly — no service, so no row lock — each list
        an empty shop and each create the full set. If this ever stopped
        duplicating, the locked test above would be proving nothing, because the
        race it claims to win would not exist.
        """
        release = asyncio.Event()
        async with live_stores() as (live,):
            desired = desired_subscriptions(WEBHOOK_TOPICS)
            held = WebhookReconciler(
                shopify.client(shop_domain=live.shop_domain, access_token="t-a")
            )
            free = WebhookReconciler(
                shopify.client(shop_domain=live.shop_domain, access_token="t-b")
            )

            shopify.hold_first_list = release
            shopify.first_list_reached = asyncio.Event()
            first = asyncio.create_task(held.reconcile(desired))
            await asyncio.wait_for(shopify.first_list_reached.wait(), timeout=HANG_GUARD_SECONDS)

            # The second reconciler runs to completion while the first is still
            # holding the empty listing it already read.
            await asyncio.wait_for(free.reconcile(desired), timeout=HANG_GUARD_SECONDS)
            release.set()
            await asyncio.wait_for(first, timeout=HANG_GUARD_SECONDS)

        assert len(shopify.creates) == 2 * len(WEBHOOK_TOPICS), (
            "the unlocked race must duplicate; if it does not, the locked test above proves nothing"
        )


class TestTenantIsolation:
    async def test_another_tenant_cannot_reconcile_this_stores_webhooks(
        self, shopify: CountingShopify
    ) -> None:
        """A foreign store id is 'not connected' — never a 403, never a call."""
        async with live_stores(2) as (owner, other):
            async with own_connection(other) as session:
                with pytest.raises(ShopifyNotConnectedError):
                    await ShopifyService(session).register_webhooks(owner.store_id)

        assert shopify.creates == []
        assert shopify.paths == [], "no Shopify request may be made for a foreign store"


class TestRegistrationStamp:
    async def test_a_healthy_reconciliation_stamps_the_connection(
        self, shopify: CountingShopify
    ) -> None:
        async with live_stores() as (live,):
            report = await reconcile_on_own_connection(live)
            assert report.healthy is True
            async with live.factory() as session:
                stamped = (
                    await session.execute(
                        sa.select(ShopifyConnection.webhooks_registered_at).where(
                            ShopifyConnection.store_id == live.store_id
                        )
                    )
                ).scalar_one()
        assert stamped is not None

    async def test_an_unhealthy_reconciliation_leaves_the_stamp_unset(
        self, shopify: CountingShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A half-registered shop must not look finished.

        Stamping regardless is what would hide a shop that never subscribed to
        ``orders/create`` behind a timestamp saying registration completed.
        """

        async def failing_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("operationName") == "WebhookSubscriptionCreate":
                shopify.creates.append(body["variables"])
                raise httpx.ReadTimeout("shopify went quiet", request=request)
            return await shopify.handler(request)

        def client(*, shop_domain: str, access_token: str, **_: Any) -> ShopifyGraphQLClient:
            return ShopifyGraphQLClient(
                shop_domain=shop_domain,
                access_token=access_token,
                transport=httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)),
            )

        monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", client)

        async with live_stores() as (live,):
            report = await reconcile_on_own_connection(live)
            assert report.healthy is False
            assert all(item.status is ReconcileStatus.UNKNOWN for item in report.items)
            async with live.factory() as session:
                stamped = (
                    await session.execute(
                        sa.select(ShopifyConnection.webhooks_registered_at).where(
                            ShopifyConnection.store_id == live.store_id
                        )
                    )
                ).scalar_one()
        assert stamped is None
        assert len(shopify.creates) == len(WEBHOOK_TOPICS), "one attempt per topic, no replay"
