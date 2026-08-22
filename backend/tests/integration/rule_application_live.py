"""Two-connection harness for rule-application races. Not a test module.

The shared ``db_session`` fixture wraps a whole test in one transaction, so two
"workers" driven through it are really one and ``FOR UPDATE`` contends with
nothing. Anything that has to prove a *lock* did its job therefore needs real,
separately-committed connections, and this is where that setup lives so the
tests themselves stay about behaviour.

**Blocking is observed, never timed.** ``pg_blocking_pids()`` is PostgreSQL's
own answer to "is this backend waiting on a lock somebody else holds", so a
test can wait for that fact rather than for a sleep that is generous enough
today and flaky on a loaded machine tomorrow. The timeouts here exist only so
a genuine hang fails the suite instead of stalling it; they are never the
thing being asserted.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import clear_context, set_tenant_id
from app.models.product import Product
from app.models.rule_application import RuleApplication, RuleApplicationItem

#: Upper bound on how long a helper will wait for a state that should arrive in
#: milliseconds. Reaching it means something is genuinely stuck, which is a
#: failure worth seeing rather than a slow machine to accommodate.
HANG_GUARD_SECONDS = 30.0


@dataclass
class LiveApplication:
    """A committed tenant, rule, drafts and application, on its own engine."""

    tenant_id: uuid.UUID
    application_id: uuid.UUID
    draft_ids: list[uuid.UUID]
    headers: dict[str, str]
    session_factory: Callable[[], AsyncSession]
    prices_before: dict[uuid.UUID, Decimal | None] = field(default_factory=dict)


async def backend_pid(session: AsyncSession) -> int:
    """The PostgreSQL backend this session is bound to."""
    return int((await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())


async def wait_until_blocked(
    factory: Callable[[], AsyncSession], pid: int, *, what: str = "backend"
) -> list[int]:
    """Block until PostgreSQL reports ``pid`` waiting on somebody else's lock.

    The assertion this supports is "the reconciler *could not* proceed", and
    that is a fact the database knows. Polling it turns a race into a
    rendezvous: the test moves on at the instant contention is real, and fails
    outright if contention never happens.
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
    raise AssertionError(f"{what} (pid {pid}) never blocked on a lock — no contention occurred")


async def assert_not_blocked(factory: Callable[[], AsyncSession], pid: int) -> None:
    """The opposite check: this backend is running freely."""
    async with factory() as observer:
        blockers = (
            await observer.execute(sa.text("SELECT pg_blocking_pids(:p)"), {"p": pid})
        ).scalar_one()
    assert not blockers, f"backend {pid} is unexpectedly blocked by {list(blockers)}"


@asynccontextmanager
async def live_application(
    *,
    drafts: int = 4,
    sell_price: str = "30.00",
    idempotency_key: str = "live",
) -> AsyncIterator[LiveApplication]:
    """Commit a whole workspace, yield it, and delete the tenant afterwards.

    Everything goes through the real API and the real service, so what the
    races run against is what production would have written. Cleanup is a
    single tenant delete, which cascades — the same approach
    ``test_product_import_concurrency`` uses.
    """
    from app.main import create_application
    from tests.integration.conftest import registration_payload
    from tests.integration.test_rule_application import APPLY, create_rule, seed_draft

    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    tenant_id: uuid.UUID | None = None
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
            await create_rule(http, headers)

            set_tenant_id(tenant_id)
            async with factory() as seeding:
                seeded = [
                    await seed_draft(seeding, tenant_id, sell_price=sell_price)
                    for _ in range(drafts)
                ]
                draft_ids = [d.id for d in seeded]
                prices = {d.id: d.sell_price for d in seeded}
                await seeding.commit()

            created = await http.post(
                APPLY,
                json={
                    "productIds": [str(d) for d in draft_ids],
                    "idempotencyKey": idempotency_key,
                },
                headers=headers,
            )
            assert created.status_code == 202, created.text
            application_id = uuid.UUID(created.json()["id"])

        set_tenant_id(tenant_id)
        yield LiveApplication(
            tenant_id=tenant_id,
            application_id=application_id,
            draft_ids=draft_ids,
            headers=headers,
            session_factory=factory,
            prices_before=prices,
        )
    finally:
        clear_context()
        if tenant_id is not None:
            async with engine.begin() as conn:
                await conn.execute(
                    sa.text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
                )
        await engine.dispose()


async def read_application(
    factory: Callable[[], AsyncSession], application_id: uuid.UUID
) -> RuleApplication:
    async with factory() as session:
        return (
            await session.execute(
                sa.select(RuleApplication)
                .where(RuleApplication.id == application_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()


async def count_items(factory: Callable[[], AsyncSession], application_id: uuid.UUID) -> int:
    async with factory() as session:
        return int(
            (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(RuleApplicationItem)
                    .where(RuleApplicationItem.application_id == application_id)
                )
            ).scalar_one()
        )


async def item_targets(
    factory: Callable[[], AsyncSession], application_id: uuid.UUID
) -> list[uuid.UUID | None]:
    """Every product a result row was written for, duplicates included."""
    async with factory() as session:
        return list(
            (
                await session.execute(
                    sa.select(RuleApplicationItem.product_id)
                    .where(RuleApplicationItem.application_id == application_id)
                    .where(RuleApplicationItem.variant_id.is_(None))
                )
            )
            .scalars()
            .all()
        )


async def prices_now(
    factory: Callable[[], AsyncSession], draft_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal | None]:
    async with factory() as session:
        rows = (
            await session.execute(
                sa.select(Product.id, Product.sell_price)
                .where(Product.id.in_(draft_ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    return {row[0]: row[1] for row in rows}
