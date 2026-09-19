"""Live lock order: every Stage 7 activation takes Product before ProductVersion."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import sqlalchemy as sa

from app.core.exceptions import ConflictError
from app.models.product import Product, ProductAIStatus, ProductVersion, ProductVersionSource
from app.repositories.product import ProductRepository, ProductVersionRepository
from app.services.product_optimization import ProductOptimizationService
from app.services.product_pipeline import ProductPipelineService
from tests.integration.live_locks import HANG_GUARD_SECONDS, backend_pid, wait_until_blocked
from tests.integration.shopify_publish_live import live_publish_targets, own_publish_session
from tests.integration.test_product_pipeline import _insert_pipeline_candidate

pytestmark = pytest.mark.integration


async def _seed_original(session: Any, product_id: Any) -> ProductVersion:
    product = await ProductRepository(session).get_by_id_or_raise(product_id)
    versions = ProductVersionRepository(session)
    return await versions.create(
        product_id=product.id,
        version_number=await versions.next_version_number(product.id),
        source=ProductVersionSource.ORIGINAL,
        content={"title": product.title, "description": product.description or ""},
        active=True,
        ai_provider=None,
        prompt_execution_id=None,
        created_by_user_id=None,
    )


async def _seed_legacy_ai(session: Any, product_id: Any) -> ProductVersion:
    product = await ProductRepository(session).get_by_id_or_raise(product_id)
    versions = ProductVersionRepository(session)
    return await versions.create(
        product_id=product.id,
        version_number=await versions.next_version_number(product.id),
        source=ProductVersionSource.AI_GENERATED,
        content={"title": "Legacy AI title", "description": "Legacy AI body"},
        active=False,
        ai_provider="stub",
        prompt_execution_id=None,
        created_by_user_id=None,
    )


async def _relation_locks(factory: Any, pid: int) -> list[tuple[str, str, str, bool]]:
    async with factory() as observer:
        rows = (
            await observer.execute(
                sa.text(
                    """
                    SELECT c.relname, l.locktype, l.mode, l.granted
                    FROM pg_locks l
                    JOIN pg_class c ON c.oid = l.relation
                    WHERE l.pid = :pid
                      AND c.relname IN ('products', 'product_versions')
                    """
                ),
                {"pid": pid},
            )
        ).all()
    return [
        (str(name), str(locktype), str(mode), bool(granted))
        for name, locktype, mode, granted in rows
    ]


class TestActivationLockOrder:
    async def test_product_lock_blocks_legacy_activate_before_version_write(self) -> None:
        """Pipeline-approve order: holding Product forces legacy activate to wait."""
        holder_ready = asyncio.Event()
        release_holder = asyncio.Event()
        activate_entered = asyncio.Event()
        activate_pid: list[int] = []

        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                await _seed_original(session, live.product_id)
                legacy = await _seed_legacy_ai(session, live.product_id)
                await session.commit()
                legacy_id = legacy.id

            async def _hold_product() -> None:
                async with own_publish_session(live) as session:
                    locked = await ProductRepository(session).lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release_holder.wait(), timeout=HANG_GUARD_SECONDS)

            async def _legacy_activate() -> Product:
                async with own_publish_session(live) as session:
                    activate_pid.append(await backend_pid(session))
                    activate_entered.set()
                    product = await ProductOptimizationService(session).activate_version(
                        live.product_id, legacy_id
                    )
                    await session.commit()
                    return product

            holder = asyncio.create_task(_hold_product())
            waiter: asyncio.Task[Product] | None = None
            try:
                await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)
                waiter = asyncio.create_task(_legacy_activate())
                await asyncio.wait_for(activate_entered.wait(), timeout=HANG_GUARD_SECONDS)
                await wait_until_blocked(live.factory, activate_pid[0])

                locks = await _relation_locks(live.factory, activate_pid[0])
                assert not any(
                    name == "product_versions" and granted is True and mode == "RowExclusiveLock"
                    for name, _lt, mode, granted in locks
                )

                async with own_publish_session(live) as session:
                    still = await ProductVersionRepository(session).get_by_id_for_product(
                        product_id=live.product_id,
                        version_id=legacy_id,
                        populate_existing=True,
                    )
                assert still is not None
                assert still.active is False

                release_holder.set()
                await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)
                activated = await asyncio.wait_for(waiter, timeout=HANG_GUARD_SECONDS)
            finally:
                release_holder.set()
                if not holder.done():
                    await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)
                if waiter is not None and not waiter.done():
                    waiter.cancel()

        assert activated.optimized_title == "Legacy AI title"

    async def test_product_lock_blocks_optimize_activation_after_generation(self) -> None:
        """Generation stays outside FOR UPDATE; activation still waits on Product."""
        holder_ready = asyncio.Event()
        release_holder = asyncio.Event()
        optimize_entered = asyncio.Event()
        optimize_pid: list[int] = []

        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                await _seed_original(session, live.product_id)
                await session.commit()

            async def _hold_product() -> None:
                async with own_publish_session(live) as session:
                    locked = await ProductRepository(session).lock_for_update(live.product_id)
                    assert locked is not None
                    holder_ready.set()
                    await asyncio.wait_for(release_holder.wait(), timeout=HANG_GUARD_SECONDS)

            async def _optimize() -> tuple[Product, ProductVersion]:
                async with own_publish_session(live) as session:
                    optimize_pid.append(await backend_pid(session))
                    optimize_entered.set()
                    product, version = await ProductOptimizationService(session).optimize_product(
                        live.product_id, requested_by_user_id=None
                    )
                    await session.commit()
                    return product, version

            holder = asyncio.create_task(_hold_product())
            waiter: asyncio.Task[tuple[Product, ProductVersion]] | None = None
            try:
                await asyncio.wait_for(holder_ready.wait(), timeout=HANG_GUARD_SECONDS)
                waiter = asyncio.create_task(_optimize())
                await asyncio.wait_for(optimize_entered.wait(), timeout=HANG_GUARD_SECONDS)
                await wait_until_blocked(live.factory, optimize_pid[0])

                async with own_publish_session(live) as session:
                    current = await ProductVersionRepository(session).get_active(live.product_id)
                assert current is not None
                assert current.source is ProductVersionSource.ORIGINAL

                release_holder.set()
                await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)
                product, version = await asyncio.wait_for(waiter, timeout=HANG_GUARD_SECONDS)
            finally:
                release_holder.set()
                if not holder.done():
                    await asyncio.wait_for(holder, timeout=HANG_GUARD_SECONDS)
                if waiter is not None and not waiter.done():
                    waiter.cancel()

        assert version.active is True
        assert "pipelineCandidateVersion" not in version.content
        assert "pipelineSourceUpdatedAt" not in version.content
        assert "isSynthetic" not in version.content
        assert product.ai_status is ProductAIStatus.OPTIMIZED

    async def test_pipeline_approve_and_legacy_activate_do_not_deadlock(self) -> None:
        async with live_publish_targets() as (live,):
            async with own_publish_session(live) as session:
                await _seed_original(session, live.product_id)
                legacy = await _seed_legacy_ai(session, live.product_id)
                product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
                candidate = await _insert_pipeline_candidate(
                    session, product, is_synthetic=False, ai_provider="test"
                )
                await session.commit()
                token = product.updated_at
                legacy_id = legacy.id
                candidate_id = candidate.id

            async def _approve() -> Product | ConflictError:
                async with own_publish_session(live) as session:
                    try:
                        approved = await ProductPipelineService(session).approve(
                            live.product_id,
                            version_id=candidate_id,
                            expected_updated_at=token,
                        )
                        await session.commit()
                    except ConflictError as exc:
                        return exc
                    return approved

            async def _activate() -> Product:
                async with own_publish_session(live) as session:
                    product = await ProductOptimizationService(session).activate_version(
                        live.product_id, legacy_id
                    )
                    await session.commit()
                    return product

            outcomes = await asyncio.wait_for(
                asyncio.gather(_approve(), _activate(), return_exceptions=True),
                timeout=HANG_GUARD_SECONDS,
            )

        for outcome in outcomes:
            assert not isinstance(outcome, BaseException) or isinstance(outcome, ConflictError)
            assert "deadlock" not in str(outcome).lower()
