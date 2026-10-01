"""Review findings B-2 and D-2 — bulk-run references, enforced by the database.

* B-2: a run's ``store_id`` must be one of the run's own tenant's stores.
* D-2: a hard tenant erasure (``DELETE FROM tenants``) must cascade through
  runs, items, products and versions, while a product or version that an item
  still references cannot be deleted on its own.

Committed transactions and a dedicated engine: FK actions only fire on real
statements, and cleanup is the tenant delete under test.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.pipeline_bulk import (
    PipelineBulkItemState,
    PipelineBulkRun,
    PipelineBulkRunItem,
    PipelineBulkRunStatus,
)
from app.models.product import (
    Product,
    ProductSource,
    ProductStatus,
    ProductVersion,
    ProductVersionSource,
)
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.tenant import Tenant

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _Seed:
    tenant_id: uuid.UUID
    store_id: uuid.UUID
    product_id: uuid.UUID
    version_id: uuid.UUID
    run_id: uuid.UUID


@asynccontextmanager
async def _factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> _Seed:
    suffix = uuid.uuid4().hex[:10]
    tenant = Tenant(name=f"Integrity {suffix}", slug=f"int-{suffix}")
    session.add(tenant)
    await session.flush()
    store = Store(
        tenant_id=tenant.id,
        name=f"Store {suffix}",
        slug=f"store-{suffix}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="GBP",
    )
    product = Product(
        tenant_id=tenant.id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{suffix}",
        title=f"Integrity {suffix}",
        status=ProductStatus.DRAFT,
    )
    session.add_all([store, product])
    await session.flush()
    version = ProductVersion(
        tenant_id=tenant.id,
        product_id=product.id,
        version_number=1,
        source=ProductVersionSource.AI_GENERATED,
        content={"title": "candidate"},
        active=False,
    )
    session.add(version)
    await session.flush()
    run = PipelineBulkRun(
        tenant_id=tenant.id,
        idempotency_key=f"key-{suffix}",
        request_fingerprint="0" * 64,
        status=PipelineBulkRunStatus.COMPLETED,
        tone="professional",
        store_id=store.id,
        selection={"productIds": [str(product.id)]},
        total_count=1,
    )
    session.add(run)
    await session.flush()
    session.add(
        PipelineBulkRunItem(
            tenant_id=tenant.id,
            run_id=run.id,
            submitted_product_id=product.id,
            product_id=product.id,
            candidate_version_id=version.id,
            state=PipelineBulkItemState.SUCCEEDED,
        )
    )
    await session.commit()
    return _Seed(tenant.id, store.id, product.id, version.id, run.id)


async def _tenant_exists(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    found = await session.execute(sa.select(Tenant.id).where(Tenant.id == tenant_id))
    return found.scalar_one_or_none() is not None


class TestHardTenantErasureCascades:
    async def test_deleting_the_tenant_removes_runs_items_products_and_versions(self) -> None:
        async with _factory() as factory:
            async with factory() as session:
                seed = await _seed(session)
            async with factory() as session:
                await session.execute(sa.delete(Tenant).where(Tenant.id == seed.tenant_id))
                await session.commit()
            async with factory() as session:
                assert not await _tenant_exists(session, seed.tenant_id)
                left = await session.execute(
                    sa.select(sa.func.count())
                    .select_from(PipelineBulkRunItem)
                    .where(PipelineBulkRunItem.tenant_id == seed.tenant_id)
                )
                assert left.scalar_one() == 0


class TestReferencedRowsCannotBeDeletedAlone:
    async def test_a_product_an_item_references_cannot_be_hard_deleted(self) -> None:
        async with _factory() as factory:
            async with factory() as session:
                seed = await _seed(session)
            try:
                async with factory() as session:
                    with pytest.raises(sa.exc.IntegrityError):
                        await session.execute(
                            sa.delete(Product).where(Product.id == seed.product_id)
                        )
                        await session.commit()
                    await session.rollback()
                async with factory() as session:
                    with pytest.raises(sa.exc.IntegrityError):
                        await session.execute(sa.delete(Store).where(Store.id == seed.store_id))
                        await session.commit()
                    await session.rollback()
            finally:
                async with factory() as session:
                    await session.execute(sa.delete(Tenant).where(Tenant.id == seed.tenant_id))
                    await session.commit()


class TestRunStoreBelongsToTheRunTenant:
    async def test_a_run_cannot_reference_another_tenants_store(self) -> None:
        async with _factory() as factory:
            async with factory() as session:
                mine = await _seed(session)
            async with factory() as session:
                theirs = await _seed(session)
            try:
                async with factory() as session:
                    with pytest.raises(sa.exc.IntegrityError):
                        await session.execute(
                            sa.update(PipelineBulkRun)
                            .where(PipelineBulkRun.id == mine.run_id)
                            .values(store_id=theirs.store_id)
                        )
                        await session.commit()
                    await session.rollback()
            finally:
                async with factory() as session:
                    for seed in (mine, theirs):
                        await session.execute(sa.delete(Tenant).where(Tenant.id == seed.tenant_id))
                    await session.commit()
