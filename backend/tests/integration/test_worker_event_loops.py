"""A worker process runs many ``asyncio.run`` loops against one engine.

Real Postgres. This is the shape of every Celery task (and of
``notifications.send_emails``, which loops per workspace inside one task):
several event loops in a row, each opening a transaction through the shared
``session_factory``. With the worker engine every loop must succeed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import text

from app.database import session as db_session

pytestmark = pytest.mark.integration


@pytest.fixture
def worker_engine() -> Iterator[None]:
    original_engine = db_session.engine
    original_bind = db_session.session_factory.kw.get("bind")
    db_session.use_null_pool_for_worker()
    yield
    db_session.engine = original_engine
    db_session.session_factory.configure(bind=original_bind)


async def _one_transaction() -> int:
    async with db_session.transaction() as session:
        return int((await session.execute(text("SELECT 1"))).scalar_one())


def test_many_event_loops_in_one_process_all_reach_the_database(worker_engine: None) -> None:
    results = [asyncio.run(_one_transaction()) for _ in range(5)]
    assert results == [1, 1, 1, 1, 1]
