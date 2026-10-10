"""A Redis client is bound to the event loop it first runs on, and rebuilt
for a different loop (Celery tasks run ``asyncio.run`` per attempt).

No Redis is contacted: ``from_url`` builds a client lazily and only connects
on the first command, which these tests never send.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core import redis as redis_module
from app.core.redis import RedisPurpose, get_redis

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    redis_module._clients.clear()


def test_the_same_loop_keeps_the_same_client() -> None:
    async def twice() -> tuple[int, int]:
        return id(get_redis(RedisPurpose.CACHE)), id(get_redis(RedisPurpose.CACHE))

    first, second = asyncio.run(twice())
    assert first == second


def test_a_new_loop_gets_a_new_client() -> None:
    """The failure this prevents: a task retry's second ``asyncio.run`` reusing
    a connection whose loop is closed."""

    async def one() -> int:
        return id(get_redis(RedisPurpose.RATE_LIMIT))

    first = asyncio.run(one())
    second = asyncio.run(one())
    assert first != second


def test_a_client_made_outside_a_loop_is_adopted_by_the_first_loop_then_replaced() -> None:
    outside = get_redis(RedisPurpose.SESSION)

    async def same() -> int:
        return id(get_redis(RedisPurpose.SESSION))

    assert asyncio.run(same()) == id(outside)  # adopted, not rebuilt
    assert asyncio.run(same()) != id(outside)  # a later loop gets its own


def test_purposes_are_independent() -> None:
    async def both() -> tuple[int, int]:
        return id(get_redis(RedisPurpose.CACHE)), id(get_redis(RedisPurpose.RATE_LIMIT))

    cache, rate = asyncio.run(both())
    assert cache != rate
