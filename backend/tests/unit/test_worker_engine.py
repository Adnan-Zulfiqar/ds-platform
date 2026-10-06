"""Worker processes use an engine that carries no connection between event
loops (found 2026-10-06: ``notifications.send_emails`` failed in a real
worker with "attached to a different loop"). No database needed: the
engines here are never connected."""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from app.database import session as db_session

pytestmark = pytest.mark.unit

# The module, not the attribute: ``app.workers.celery_app`` is also the name
# of the Celery object the package exports.
worker_module = importlib.import_module("app.workers.celery_app")


@pytest.fixture
def pooled_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Stand in for the production (pooled) engine, then restore."""
    original_engine = db_session.engine
    original_bind = db_session.session_factory.kw.get("bind")
    pooled = create_async_engine("postgresql+asyncpg://u:p@localhost:1/none")
    monkeypatch.setattr(db_session, "engine", pooled)
    db_session.session_factory.configure(bind=pooled)
    yield pooled
    db_session.engine = original_engine
    db_session.session_factory.configure(bind=original_bind)


def test_the_worker_switch_rebinds_the_shared_factory_to_a_pool_less_engine(
    pooled_engine: object,
) -> None:
    assert isinstance(db_session.engine.pool, AsyncAdaptedQueuePool)

    db_session.use_null_pool_for_worker()

    assert isinstance(db_session.engine.pool, NullPool)
    assert db_session.engine is not pooled_engine
    # The same factory object, re-bound: modules that imported it by name
    # (``app.services.product_import``) see the new engine too.
    assert db_session.session_factory.kw["bind"] is db_session.engine


def test_the_switch_is_idempotent(pooled_engine: object) -> None:
    db_session.use_null_pool_for_worker()
    first = db_session.engine
    db_session.use_null_pool_for_worker()
    assert db_session.engine is first


def test_both_worker_start_signals_run_the_switch() -> None:
    from celery.signals import worker_init, worker_process_init

    for signal in (worker_init, worker_process_init):
        receivers = [ref() for _, ref in signal.receivers]
        assert worker_module._worker_database in receivers
