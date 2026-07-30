"""Fixtures for tests that need a real PostgreSQL database.

**The schema is built by running the Alembic migrations**, not by
``Base.metadata.create_all()``. Creating tables from the models would test the
models against themselves and prove nothing about the migrations — which are
what actually runs against production. This way a migration that does not apply
cleanly fails the test suite rather than the deploy.

Every test runs inside a transaction that is rolled back afterwards, so tests
cannot see each other's rows and order does not matter.

The whole package skips when no database is reachable, so a developer without
PostgreSQL still gets a green unit suite instead of a wall of connection errors.
CI always has one, so these never silently stop running there.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import clear_context

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _database_is_reachable() -> bool:
    """Probe PostgreSQL with the synchronous driver.

    Synchronous on purpose: this runs at collection time, before any event loop
    exists, so an async probe would need a loop just to decide whether to skip.
    """
    try:
        engine = sa.create_engine(settings.database.sync_dsn, connect_args={"connect_timeout": 3})
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
        engine.dispose()
    except Exception:
        return False
    return True


DATABASE_AVAILABLE = _database_is_reachable()

pytestmark = pytest.mark.skipif(
    not DATABASE_AVAILABLE,
    reason="PostgreSQL is not reachable; set POSTGRES_* to run integration tests.",
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Bring the test database to head, from empty.

    Downgrading to base first means each session starts from nothing, so a
    migration that only works against an already-populated schema is caught
    here rather than on a fresh environment.
    """
    if not DATABASE_AVAILABLE:
        return

    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database.sync_dsn)

    # Tolerated and deliberately silent: a database with no alembic_version
    # table has nothing to downgrade, which is the normal state on a fresh
    # machine or in CI. Failing here would make the common case an error.
    try:
        command.downgrade(config, "base")
    except Exception:  # noqa: S110
        pass

    command.upgrade(config, "head")


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """A session whose changes are rolled back at the end of the test.

    The session is bound to an open connection-level transaction rather than
    being allowed to commit. Anything the code under test commits lands inside
    that outer transaction, and rolling it back discards the lot — including
    writes made by request handlers, which do commit.
    """
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    connection = await engine.connect()
    transaction = await connection.begin()

    factory = async_sessionmaker(bind=connection, expire_on_commit=False, autoflush=False)
    session = factory()

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()
        clear_context()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """An HTTP client wired to the application, sharing the test transaction.

    ``get_db_session`` is overridden so handlers use the same rolled-back
    session as the test body. Without that the API would write through its own
    connection and leave rows behind.

    ``ASGITransport`` calls the app in-process — no socket, no server, and none
    of the overhead that made Starlette's TestClient misleading to benchmark
    against in Phase 0.
    """
    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        # Cookies persist across calls on one client, which is what makes the
        # refresh-token flow testable exactly as a browser performs it.
    ) as http_client:
        yield http_client

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _disable_login_throttle() -> AsyncGenerator[None]:
    """Turn off login throttling for the HTTP flow tests.

    Redis may not be running, and the throttle fails open, so leaving it on
    would mostly be a no-op — but a test that legitimately makes several failed
    attempts should not depend on that.

    The throttle itself is covered by ``tests/unit/test_login_throttle.py``,
    which runs it against an in-process Redis. An earlier version of this
    docstring claimed that coverage before it existed; it exists now.
    """
    original = settings.security.rate_limit_enabled
    settings.security.rate_limit_enabled = False
    yield
    settings.security.rate_limit_enabled = original


# Registration payload reused across tests.
STRONG_PASSWORD = "Correct-Horse-Battery9"


def registration_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "companyName": "Acme Trading",
        "email": f"owner+{os.urandom(4).hex()}@example.com",
        "password": STRONG_PASSWORD,
        "firstName": "Ada",
        "lastName": "Lovelace",
    }
    payload.update(overrides)
    return payload
