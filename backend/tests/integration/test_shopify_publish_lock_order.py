"""UX-L2B-R3 — publish must revalidate under the product row lock.

While publication waits for ``SELECT ... FOR UPDATE``, another transaction may
mutate the draft (or disconnect the store) and commit. After acquiring the
lock, publish must observe current authoritative state: stale
``expectedUpdatedAt`` → 409, new blockers → zero provider calls.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from app.core.exceptions import ConflictError, ValidationError
from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.sync import ShopifySyncService
from app.models.integration import IntegrationStatus
from app.models.product import Product
from app.models.shopify import ShopifyConnection
from app.repositories.product import ProductRepository
from tests.integration.live_locks import HANG_GUARD_SECONDS, backend_pid, wait_until_blocked
from tests.integration.shopify_publish_live import (
    CountingPublishShopify,
    listing_count,
    live_publish_targets,
    own_publish_session,
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


class TestLockedStateRevalidation:
    async def test_edit_while_waiting_for_lock_yields_409_and_zero_creates(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        """Holder mutates the draft under FOR UPDATE; waiter must reject stale version."""
        async with live_publish_targets() as (live,):
            expected = live.updated_at
            holder_ready = asyncio.Event()
            release_holder = asyncio.Event()
            publish_entered = asyncio.Event()
            publish_pid: list[int] = []

            async def _hold_then_edit() -> None:
                async with own_publish_session(live) as session:
                    repo = ProductRepository(session)
                    locked = await repo.lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release_holder.wait(), timeout=HANG_GUARD_SECONDS)
                    await session.execute(
                        sa.update(Product)
                        .where(Product.id == live.product_id)
                        .values(
                            title="Edited while publish waited",
                            updated_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()

            holder = asyncio.create_task(_hold_then_edit())
            await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)

            async def _publish_waiting() -> Any:
                async with own_publish_session(live) as session:
                    publish_pid.append(await backend_pid(session))
                    publish_entered.set()
                    result = await ShopifySyncService(session).publish_product(
                        store_id=live.store_id,
                        product_id=live.product_id,
                        expected_updated_at=expected,
                    )
                    await session.commit()
                    return result

            publish_task = asyncio.create_task(_publish_waiting())
            await asyncio.wait_for(publish_entered.wait(), timeout=HANG_GUARD_SECONDS)
            blockers = await wait_until_blocked(live.factory, publish_pid[0])
            assert blockers, "publish never blocked on the product row"

            release_holder.set()
            await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)

            with pytest.raises(ConflictError) as exc_info:
                await asyncio.wait_for(publish_task, timeout=HANG_GUARD_SECONDS)

            assert exc_info.value.details.get("reason") == "draft_version_stale"
            assert shopify_wire.creates == []
            assert await listing_count(live) == 0

    async def test_destination_change_while_waiting_blocks_with_zero_creates(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        async with live_publish_targets() as (live,):
            holder_ready = asyncio.Event()
            release_holder = asyncio.Event()
            publish_entered = asyncio.Event()
            publish_pid: list[int] = []

            async def _hold_then_mismatch_destination() -> None:
                async with own_publish_session(live) as session:
                    locked = await ProductRepository(session).lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release_holder.wait(), timeout=HANG_GUARD_SECONDS)
                    await session.execute(
                        sa.update(Product)
                        .where(Product.id == live.product_id)
                        .values(
                            import_ship_to_country="US",
                            # Keep the version token stable so the post-lock
                            # check exercises destination readiness, not 409.
                            updated_at=live.updated_at,
                        )
                    )
                    await session.commit()

            holder = asyncio.create_task(_hold_then_mismatch_destination())
            await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)

            async def _publish() -> Any:
                async with own_publish_session(live) as session:
                    publish_pid.append(await backend_pid(session))
                    publish_entered.set()
                    return await ShopifySyncService(session).publish_product(
                        store_id=live.store_id,
                        product_id=live.product_id,
                        expected_updated_at=live.updated_at,
                    )

            publish_task = asyncio.create_task(_publish())
            await asyncio.wait_for(publish_entered.wait(), timeout=HANG_GUARD_SECONDS)
            await wait_until_blocked(live.factory, publish_pid[0])
            release_holder.set()
            await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)

            with pytest.raises(ValidationError) as exc_info:
                await asyncio.wait_for(publish_task, timeout=HANG_GUARD_SECONDS)
            assert "destination_mismatch" in str(exc_info.value.details.get("blocker_codes", ""))
            assert shopify_wire.creates == []

    async def test_store_disconnect_while_waiting_blocks_with_zero_creates(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        """Store connection is not product-locked; still re-read after acquire."""
        async with live_publish_targets() as (live,):
            holder_ready = asyncio.Event()
            release_holder = asyncio.Event()
            publish_entered = asyncio.Event()
            publish_pid: list[int] = []

            async def _hold_product_disconnect_store() -> None:
                async with own_publish_session(live) as session:
                    locked = await ProductRepository(session).lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release_holder.wait(), timeout=HANG_GUARD_SECONDS)
                    await session.execute(
                        sa.update(ShopifyConnection)
                        .where(ShopifyConnection.store_id == live.store_id)
                        .values(status=IntegrationStatus.ERROR)
                    )
                    await session.commit()

            holder = asyncio.create_task(_hold_product_disconnect_store())
            await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)

            async def _publish() -> Any:
                async with own_publish_session(live) as session:
                    publish_pid.append(await backend_pid(session))
                    publish_entered.set()
                    return await ShopifySyncService(session).publish_product(
                        store_id=live.store_id,
                        product_id=live.product_id,
                        expected_updated_at=live.updated_at,
                    )

            publish_task = asyncio.create_task(_publish())
            await asyncio.wait_for(publish_entered.wait(), timeout=HANG_GUARD_SECONDS)
            await wait_until_blocked(live.factory, publish_pid[0])
            release_holder.set()
            await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)

            with pytest.raises(ValidationError) as exc_info:
                await asyncio.wait_for(publish_task, timeout=HANG_GUARD_SECONDS)
            assert "store_disconnected" in str(exc_info.value.details.get("blocker_codes", ""))
            assert shopify_wire.creates == []

    async def test_soft_deleted_draft_while_waiting_is_not_found(
        self, shopify_wire: CountingPublishShopify
    ) -> None:
        from app.core.exceptions import NotFoundError

        async with live_publish_targets() as (live,):
            holder_ready = asyncio.Event()
            release_holder = asyncio.Event()
            publish_entered = asyncio.Event()
            publish_pid: list[int] = []

            async def _hold_then_soft_delete() -> None:
                async with own_publish_session(live) as session:
                    locked = await ProductRepository(session).lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release_holder.wait(), timeout=HANG_GUARD_SECONDS)
                    await session.execute(
                        sa.update(Product)
                        .where(Product.id == live.product_id)
                        .values(deleted_at=datetime.now(UTC))
                    )
                    await session.commit()

            holder = asyncio.create_task(_hold_then_soft_delete())
            await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)

            async def _publish() -> Any:
                async with own_publish_session(live) as session:
                    publish_pid.append(await backend_pid(session))
                    publish_entered.set()
                    return await ShopifySyncService(session).publish_product(
                        store_id=live.store_id,
                        product_id=live.product_id,
                        expected_updated_at=live.updated_at,
                    )

            publish_task = asyncio.create_task(_publish())
            await asyncio.wait_for(publish_entered.wait(), timeout=HANG_GUARD_SECONDS)
            await wait_until_blocked(live.factory, publish_pid[0])
            release_holder.set()
            await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)

            with pytest.raises(NotFoundError):
                await asyncio.wait_for(publish_task, timeout=HANG_GUARD_SECONDS)
            assert shopify_wire.creates == []
