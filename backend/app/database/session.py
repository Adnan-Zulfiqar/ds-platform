"""Database engine and session management.

The application is fully async, so it uses ``asyncpg`` through SQLAlchemy's async
engine. Alembic and Celery run synchronously and get their own engine built from
``sync_dsn``; sharing one engine across both worlds is not possible.

Session lifecycle follows the unit-of-work pattern: one session per request, one
transaction per request, committed if the handler returns normally and rolled
back if it raises. Handlers therefore do not call ``commit()`` themselves, which
removes a whole class of bug where a request half-commits and leaves the
database inconsistent.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Environment, settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _create_engine() -> AsyncEngine:
    """Build the async engine with environment-appropriate pooling.

    Tests use ``NullPool``: pooled connections outlive an individual test and
    hold transactions open, which makes test isolation unreliable. The cost of
    reconnecting per test is irrelevant at test volumes.
    """
    if settings.environment is Environment.TEST:
        return create_async_engine(
            settings.database.async_dsn,
            echo=settings.database.echo_sql,
            poolclass=NullPool,
            future=True,
        )

    db = settings.database
    return create_async_engine(
        db.async_dsn,
        echo=db.echo_sql,
        pool_size=db.pool_size,
        max_overflow=db.max_overflow,
        pool_timeout=db.pool_timeout,
        pool_recycle=db.pool_recycle,
        pool_pre_ping=db.pool_pre_ping,
        future=True,
        connect_args={
            # Statement caching is disabled because PgBouncer in transaction
            # pooling mode reuses server connections across clients, which
            # invalidates asyncpg's per-connection prepared-statement cache.
            "statement_cache_size": 0,
            # TLS. asyncpg takes a context, not libpq's `sslmode`, so the
            # translation lives in one place — `DatabaseSettings.ssl_parameter`
            # — and the synchronous engine Alembic uses reads the same setting.
            # A managed database is reached over a network this application does
            # not own; `verify-full` is what makes the connection authenticated
            # rather than merely encrypted.
            "ssl": db.ssl_parameter(),
        },
    )


engine: AsyncEngine = _create_engine()

session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # Objects stay usable after commit. With the default (True), touching any
    # attribute post-commit triggers a lazy refresh, which raises in async code
    # because the implicit IO has no await point.
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def transaction() -> AsyncGenerator[AsyncSession]:
    """Provide a session wrapped in a single transaction.

    Used outside the request cycle — Celery tasks, management scripts, seeds.
    Inside a request, depend on ``app.api.deps.get_db_session`` instead so that
    the session is tied to request scope.
    """
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_database_health() -> bool:
    """Return whether the database answers a trivial query.

    Used by the readiness probe. Deliberately narrow: it proves the pool can
    hand out a working connection, nothing more.
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("database_health_check_failed", error=str(exc))
        return False
    return True


async def dispose_engine() -> None:
    """Close all pooled connections. Called on application shutdown."""
    await engine.dispose()
    logger.info("database_engine_disposed")
