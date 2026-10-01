"""Review finding C-1 — OAuth state and install tickets are consumed atomically.

The consumers used GET then DELETE: two concurrent callbacks carrying the
same state could both read it before either deleted it. They now use
``take_once`` (MULTI/EXEC), which works on the supported Redis 3.0.504
baseline where GETDEL does not exist.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest
from fakeredis import aioredis as fake_aioredis

from app.core.redis import take_once
from app.integrations.aliexpress import service as aliexpress_service
from app.integrations.shopify import service as shopify_service
from app.integrations.shopify.exceptions import ShopifyOAuthStateError

pytestmark = pytest.mark.unit


@pytest.fixture
def redis() -> fake_aioredis.FakeRedis:
    return fake_aioredis.FakeRedis(decode_responses=True)


class TestTakeOnce:
    async def test_returns_the_value_once_then_nothing(
        self, redis: fake_aioredis.FakeRedis
    ) -> None:
        await redis.set("k", "v")
        assert await take_once(redis, "k") == "v"
        assert await take_once(redis, "k") is None
        assert await redis.exists("k") == 0

    async def test_concurrent_callers_exactly_one_wins(
        self, redis: fake_aioredis.FakeRedis
    ) -> None:
        await redis.set("state", "payload")
        results = await asyncio.gather(*(take_once(redis, "state") for _ in range(25)))
        assert results.count("payload") == 1
        assert results.count(None) == 24

    async def test_a_missing_key_is_none(self, redis: fake_aioredis.FakeRedis) -> None:
        assert await take_once(redis, "never-issued") is None


class TestShopifyConsumers:
    async def test_state_is_single_use(
        self, redis: fake_aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shopify_service, "get_redis", lambda _purpose: redis)
        await redis.set(f"{shopify_service._STATE_KEY_PREFIX}abc", json.dumps({"tenant_id": "t"}))
        service = shopify_service.ShopifyService.__new__(shopify_service.ShopifyService)

        first = await service._consume_state("abc")
        assert first == {"tenant_id": "t"}
        with pytest.raises(ShopifyOAuthStateError):
            await service._consume_state("abc")

    async def test_concurrent_callbacks_with_one_state_admit_one(
        self, redis: fake_aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shopify_service, "get_redis", lambda _purpose: redis)
        await redis.set(f"{shopify_service._STATE_KEY_PREFIX}race", json.dumps({"tenant_id": "t"}))
        service = shopify_service.ShopifyService.__new__(shopify_service.ShopifyService)

        outcomes = await asyncio.gather(
            *(service._consume_state("race") for _ in range(10)), return_exceptions=True
        )
        assert sum(isinstance(o, dict) for o in outcomes) == 1
        assert sum(isinstance(o, ShopifyOAuthStateError) for o in outcomes) == 9


class TestRedisBaseline:
    @pytest.mark.parametrize(
        "function",
        [
            shopify_service.ShopifyService._consume_state,
            shopify_service.ShopifyService._consume_install_ticket,
            aliexpress_service.AliExpressService._consume_state,
            take_once,
        ],
    )
    def test_no_command_newer_than_redis_3_0(self, function: object) -> None:
        source = inspect.getsource(function).lower()  # type: ignore[arg-type]
        assert "getdel" not in source.replace("`getdel`", "")
        assert "client.get(key)" not in source, "a separate GET reopens the race"
