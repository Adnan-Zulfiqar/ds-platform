"""Fixtures for tests that need a real PostgreSQL database.

**The schema is built by running the Alembic migrations**, not by
``Base.metadata.create_all()``. Creating tables from the models would test the
models against themselves and prove nothing about the migrations — which are
what actually runs against production. This way a migration that does not apply
cleanly fails the test suite rather than the deploy.

Every test runs inside a transaction that is rolled back afterwards, so tests
cannot see each other's rows and order does not matter.

**Isolated test database.** Root ``tests/conftest.py`` already sets
``POSTGRES_DB=droppilot_test`` before Settings is constructed, so integration
tests never use the shared developer ``droppilot`` database. This module ensures
that database exists and applies migrations to head.

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
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import clear_context
from app.integrations.aliexpress import service as aliexpress_service_module

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_TEST_DB = settings.database.db


def _ensure_test_database_exists() -> None:
    """Create the configured test database if missing."""
    admin_url = (
        f"postgresql+psycopg://{settings.database.user}:"
        f"{settings.database.password.get_secret_value()}"
        f"@{settings.database.host}:{settings.database.port}/postgres"
    )
    engine = sa.create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": _TEST_DB},
            ).scalar()
            if not exists:
                connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DB}"'))
    finally:
        engine.dispose()


def _database_is_reachable() -> bool:
    """Probe PostgreSQL with the synchronous driver.

    Synchronous on purpose: this runs at collection time, before any event loop
    exists, so an async probe would need a loop just to decide whether to skip.
    """
    try:
        _ensure_test_database_exists()
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
    """Bring the isolated test database to head, from empty."""
    if not DATABASE_AVAILABLE:
        return

    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database.sync_dsn)

    try:
        command.downgrade(config, "base")
    except Exception:  # noqa: S110
        pass

    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
def _platform_import_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Platform AE credentials + ship-to fallback for import integration tests.

    Root ``tests/conftest.py`` disables ``.env`` reads, so connect/import cannot
    depend on a developer machine's AliExpress keys. Tests that omit
    ``shipToCountry`` still need a resolvable destination.
    """
    monkeypatch.setattr(
        aliexpress_service_module.settings.aliexpress,
        "app_key",
        "test-aliexpress-app-key",
    )
    monkeypatch.setattr(
        aliexpress_service_module.settings.aliexpress,
        "app_secret",
        SecretStr("test-aliexpress-app-secret"),
    )
    monkeypatch.setattr(
        aliexpress_service_module.settings,
        "default_ship_to_country",
        "US",
    )


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """A session whose changes are rolled back at the end of the test."""
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
    """An HTTP client wired to the application, sharing the test transaction."""
    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as http_client:
        yield http_client

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _disable_login_throttle() -> AsyncGenerator[None]:
    """Turn off login throttling for the HTTP flow tests."""
    original = settings.security.rate_limit_enabled
    settings.security.rate_limit_enabled = False
    yield
    settings.security.rate_limit_enabled = original


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
