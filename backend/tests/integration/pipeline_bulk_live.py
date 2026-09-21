"""Committed connections for pipeline-bulk races. Not a test module."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import set_tenant_id
from app.models.product import Product, ProductSource, ProductStatus
from app.tasks import ai as ai_tasks

HANG_GUARD_SECONDS = 30.0


@dataclass
class LiveBulk:
    tenant_id: uuid.UUID
    headers: dict[str, str]
    product_ids: list[uuid.UUID]
    session_factory: Callable[[], AsyncSession]
    run_id: uuid.UUID | None = None


async def backend_pid(session: AsyncSession) -> int:
    return int((await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())


async def wait_until_blocked(
    factory: Callable[[], AsyncSession], pid: int, *, what: str = "backend"
) -> list[int]:
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while time.monotonic() < deadline:
        async with factory() as observer:
            blockers = (
                await observer.execute(sa.text("SELECT pg_blocking_pids(:p)"), {"p": pid})
            ).scalar_one()
        if blockers:
            return list(blockers)
        await asyncio.sleep(0.01)
    raise AssertionError(f"{what} (pid {pid}) never blocked on a lock — no contention occurred")


async def _seed_product(session: AsyncSession, tenant_id: uuid.UUID) -> Product:
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"bulk-{uuid.uuid4().hex[:10]}",
        title="Bulk pipeline product",
        status=ProductStatus.DRAFT,
    )
    session.add(product)
    await session.flush()
    return product


@asynccontextmanager
async def live_bulk(
    *,
    products: int = 1,
    create_run: bool = True,
    idempotency_key: str = "live-bulk",
) -> AsyncIterator[LiveBulk]:
    from app.main import create_application
    from tests.integration.conftest import registration_payload
    from tests.integration.pipeline_bulk_harness import EnqueueRecorder

    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    tenant_id: uuid.UUID | None = None
    recorder = EnqueueRecorder()
    original_enqueue = ai_tasks.enqueue
    ai_tasks.enqueue = recorder  # type: ignore[method-assign]
    try:
        app = create_application()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            registered = await http.post("/api/v1/auth/register", json=registration_payload())
            assert registered.status_code == 201, registered.text
            identity = registered.json()
            tenant_id = uuid.UUID(identity["identity"]["tenant"]["id"])
            headers = {"Authorization": f"Bearer {identity['tokens']['accessToken']}"}

            set_tenant_id(tenant_id)
            async with factory() as seeding:
                seeded = [await _seed_product(seeding, tenant_id) for _ in range(products)]
                product_ids = [item.id for item in seeded]
                await seeding.commit()

            run_id: uuid.UUID | None = None
            if create_run:
                created = await http.post(
                    "/api/v1/products/pipeline/runs",
                    json={
                        "productIds": [str(pid) for pid in product_ids],
                        "idempotencyKey": idempotency_key,
                    },
                    headers=headers,
                )
                assert created.status_code == 202, created.text
                run_id = uuid.UUID(created.json()["id"])

        set_tenant_id(tenant_id)
        yield LiveBulk(
            tenant_id=tenant_id,
            headers=headers,
            product_ids=product_ids,
            session_factory=factory,
            run_id=run_id,
        )
    finally:
        ai_tasks.enqueue = original_enqueue  # type: ignore[method-assign]
        if tenant_id is not None:
            async with factory() as cleanup:
                await cleanup.execute(
                    sa.text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id}
                )
                await cleanup.commit()
        await engine.dispose()
