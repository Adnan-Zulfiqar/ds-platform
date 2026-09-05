"""UX-L2B-R2 — concurrent Shopify publishes create at most one remote product.

Race on ``fd46854`` (pre-lock):

```text
Request A                         Request B
check StoreListing               check StoreListing
find none                         find none
search deterministic handle      search deterministic handle
find none                         find none
create Shopify product           create Shopify product
persist StoreListing             persist StoreListing
```

Fix: tenant-scoped ``SELECT ... FOR UPDATE`` on the product row after readiness
and before provider contact; re-read ``StoreListing`` under the lock.

Two real PostgreSQL connections; Shopify REST wire faked with barriers.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ShopifyPublishBusyError,
    ValidationError,
)
from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.sync import ShopifySyncService
from app.models.integration import IntegrationStatus
from app.models.product import Product
from app.models.shopify import ShopifyConnection
from app.repositories.product import ProductRepository
from tests.integration.shopify_publish_live import (
    HANG_GUARD_SECONDS,
    CountingPublishShopify,
    LivePublishTarget,
    backend_pid,
    listing_count,
    live_publish_targets,
    own_publish_session,
    wait_until_blocked,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def shopify_wire(monkeypatch: pytest.MonkeyPatch) -> CountingPublishShopify:
    wire = CountingPublishShopify()
    monkeypatch.setattr(
        shopify_service_module,
        "ShopifyClient",
        lambda **kwargs: wire.client(**kwargs),
    )
    return wire


async def _publish(
    live: LivePublishTarget,
    *,
    store_id: Any | None = None,
    expected_updated_at: Any | None = ...,
    ready: asyncio.Event | None = None,
    pid_out: list[int] | None = None,
) -> dict[str, Any]:
    async with own_publish_session(live) as session:
        if pid_out is not None:
            pid_out.append(await backend_pid(session))
        if ready is not None:
            ready.set()
        result = await ShopifySyncService(session).publish_product(
            store_id=store_id or live.store_id,
            product_id=live.product_id,
            expected_updated_at=(
                live.updated_at if expected_updated_at is ... else expected_updated_at
            ),
        )
        await session.commit()
        return result


class TestConcurrentSamePublication:
    async def test_two_concurrent_publishes_create_exactly_one_remote_product(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        """Central invariant: at most one provider create under real contention."""
        release = asyncio.Event()
        async with live_publish_targets() as (live,):
            shopify_wire.hold_first_create = release
            shopify_wire.first_create_reached = asyncio.Event()

            winner = asyncio.create_task(_publish(live))
            await asyncio.wait_for(
                shopify_wire.first_create_reached.wait(), timeout=HANG_GUARD_SECONDS
            )

            loser_ready = asyncio.Event()
            loser_pid: list[int] = []
            loser = asyncio.create_task(_publish(live, ready=loser_ready, pid_out=loser_pid))
            await asyncio.wait_for(loser_ready.wait(), timeout=HANG_GUARD_SECONDS)

            blockers = await wait_until_blocked(live.factory, loser_pid[0])
            assert blockers, "second publish never blocked on the product row lock"

            release.set()
            first = await asyncio.wait_for(winner, timeout=HANG_GUARD_SECONDS)
            second = await asyncio.wait_for(loser, timeout=HANG_GUARD_SECONDS)

            assert len(shopify_wire.creates) == 1, (
                f"expected exactly one provider create, got {len(shopify_wire.creates)}"
            )
            assert await listing_count(live) == 1
            assert first["external_product_id"] == second["external_product_id"]
            assert first["listing_id"]
            assert second["listing_id"]

    @pytest.mark.parametrize("run", range(3))
    async def test_concurrent_publish_is_stable_across_repeats(
        self, shopify_wire: CountingPublishShopify, run: int
    ) -> None:
        release = asyncio.Event()
        async with live_publish_targets() as (live,):
            shopify_wire.hold_first_create = release
            shopify_wire.first_create_reached = asyncio.Event()
            a = asyncio.create_task(_publish(live))
            await asyncio.wait_for(
                shopify_wire.first_create_reached.wait(), timeout=HANG_GUARD_SECONDS
            )
            b_ready = asyncio.Event()
            b_pid: list[int] = []
            b = asyncio.create_task(_publish(live, ready=b_ready, pid_out=b_pid))
            await asyncio.wait_for(b_ready.wait(), timeout=HANG_GUARD_SECONDS)
            await wait_until_blocked(live.factory, b_pid[0])
            release.set()
            await asyncio.gather(
                asyncio.wait_for(a, timeout=HANG_GUARD_SECONDS),
                asyncio.wait_for(b, timeout=HANG_GUARD_SECONDS),
            )
            assert len(shopify_wire.creates) == 1, f"flaky on repeat {run}"
            assert await listing_count(live) == 1

    async def test_unlocked_race_still_duplicates(
        self, shopify_wire: CountingPublishShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: without FOR UPDATE the same barrier race creates twice.

        If this stopped duplicating, the locked test above would prove nothing.
        """

        async def _no_lock(
            self: ProductRepository, product_id: Any, *, timeout_ms: int | None = None
        ) -> Product | None:
            result = await self.session.execute(self._base_query().where(Product.id == product_id))
            return result.scalar_one_or_none()

        monkeypatch.setattr(ProductRepository, "lock_for_update", _no_lock)

        release = asyncio.Event()
        async with live_publish_targets() as (live,):
            shopify_wire.hold_first_create = release
            shopify_wire.first_create_reached = asyncio.Event()

            first = asyncio.create_task(_publish(live))
            await asyncio.wait_for(
                shopify_wire.first_create_reached.wait(), timeout=HANG_GUARD_SECONDS
            )
            # Second finishes while first is still held inside create — both
            # create remotely; the second StoreListing insert may conflict.
            second_outcome = await asyncio.gather(_publish(live), return_exceptions=True)
            release.set()
            first_outcome = await asyncio.gather(first, return_exceptions=True)

            assert len(shopify_wire.creates) == 2, (
                "unlocked race must duplicate; otherwise the locked test is vacuous "
                f"(creates={shopify_wire.creates}, first={first_outcome!r}, "
                f"second={second_outcome!r})"
            )


class TestDifferentIdentities:
    async def test_same_product_different_stores_each_create_once(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets(with_second_store=True) as (live,):
            assert live.second_store_id is not None
            a = await _publish(live, store_id=live.store_id)
            b = await _publish(live, store_id=live.second_store_id)
            assert len(shopify_wire.creates) == 2
            assert a["external_product_id"] != b["external_product_id"]
            assert await listing_count(live, store_id=live.store_id) == 1
            assert await listing_count(live, store_id=live.second_store_id) == 1


class TestForeignAndGuards:
    async def test_cross_tenant_publish_never_creates(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets(count=2) as (owner, other):
            async with own_publish_session(other) as session:
                with pytest.raises(NotFoundError):
                    await ShopifySyncService(session).publish_product(
                        store_id=other.store_id,
                        product_id=owner.product_id,
                        expected_updated_at=owner.updated_at,
                    )
        assert shopify_wire.creates == []

    async def test_stale_version_performs_zero_provider_creates(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            stale = live.updated_at - timedelta(seconds=30)
            with pytest.raises(ConflictError):
                await _publish(live, expected_updated_at=stale)
        assert shopify_wire.creates == []

    async def test_disconnected_store_performs_zero_provider_creates(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            async with live.factory() as session:
                await session.execute(
                    sa.update(ShopifyConnection)
                    .where(ShopifyConnection.store_id == live.store_id)
                    .values(status=IntegrationStatus.ERROR)
                )
                await session.commit()
            with pytest.raises(ValidationError):
                await _publish(live)
        assert shopify_wire.creates == []


class TestFailureAndRecovery:
    async def test_provider_create_failure_allows_retry_create_once(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            shopify_wire.fail_next_create_with = RuntimeError("provider refused")
            with pytest.raises(RuntimeError):
                await _publish(live)
            assert len(shopify_wire.creates) == 0
            result = await _publish(live)
            assert len(shopify_wire.creates) == 1
            assert result["external_product_id"]

    async def test_lost_response_after_remote_create_adopts_on_retry(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            shopify_wire.drop_create_response = True
            with pytest.raises(TimeoutError):
                await _publish(live)
            assert len(shopify_wire.creates) == 1
            assert await listing_count(live) == 0
            result = await _publish(live)
            assert len(shopify_wire.creates) == 1, "retry must adopt, not create again"
            assert await listing_count(live) == 1
            assert result["external_product_id"] == shopify_wire.creates[0]["id"]

    async def test_lock_timeout_returns_stable_busy_error(
        self, shopify_wire: CountingPublishShopify, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.integrations.shopify.sync.PUBLISH_LOCK_TIMEOUT_MS",
            50,
        )
        release = asyncio.Event()
        async with live_publish_targets() as (live,):
            shopify_wire.hold_first_create = release
            shopify_wire.first_create_reached = asyncio.Event()
            holder = asyncio.create_task(_publish(live))
            await asyncio.wait_for(
                shopify_wire.first_create_reached.wait(), timeout=HANG_GUARD_SECONDS
            )
            with pytest.raises(ShopifyPublishBusyError) as exc_info:
                await _publish(live)
            assert exc_info.value.code == "shopify_publish_busy"
            release.set()
            await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)
            assert len(shopify_wire.creates) == 1

    async def test_rollback_releases_lock_for_next_publish(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            shopify_wire.fail_next_create_with = RuntimeError("boom")
            with pytest.raises(RuntimeError):
                await _publish(live)
            # Lock must not leak — a follow-up publish proceeds.
            result = await _publish(live)
            assert result["external_product_id"]
            assert len(shopify_wire.creates) == 1
