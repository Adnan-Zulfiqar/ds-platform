"""Redis clients.

Three logically separate concerns share one Redis server but not one database:
caching, sessions, and rate limiting. Separating them means ``FLUSHDB`` on the
cache — a routine operation during a bad deploy — cannot sign every customer out
or reset every rate-limit counter.

Clients are created lazily and reused. Connection pools are expensive to build
and a per-call client would exhaust file descriptors under load.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio.client import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import CacheError
from app.core.logging import get_logger

logger = get_logger(__name__)


class RedisPurpose(StrEnum):
    """Logical database selector."""

    CACHE = "cache"
    SESSION = "session"
    RATE_LIMIT = "rate_limit"


#: ``Redis[str]`` because every client is built with ``decode_responses=True``,
#: so replies arrive as ``str`` rather than ``bytes``.
_clients: dict[RedisPurpose, Redis[str]] = {}


def _dsn_for(purpose: RedisPurpose) -> str:
    match purpose:
        case RedisPurpose.CACHE:
            return settings.redis.cache_dsn
        case RedisPurpose.SESSION:
            return settings.redis.session_dsn
        case RedisPurpose.RATE_LIMIT:
            return settings.redis.rate_limit_dsn


def get_redis(purpose: RedisPurpose = RedisPurpose.CACHE) -> Redis[str]:
    """Return the shared client for a purpose, creating it on first use.

    The rate-limit client uses a much shorter timeout than the others because it
    sits on the per-request hot path; see ``fast_path_timeout_seconds``.
    """
    client = _clients.get(purpose)
    if client is None:
        timeout: float = (
            settings.redis.fast_path_timeout_seconds
            if purpose is RedisPurpose.RATE_LIMIT
            else settings.redis.socket_timeout_seconds
        )
        client = aioredis.from_url(
            _dsn_for(purpose),
            encoding="utf-8",
            decode_responses=True,
            # RESP3 negotiates with HELLO, which Redis 5 and some Windows ports
            # reject. RESP2 is sufficient for every command this application
            # uses and keeps local development working against older servers.
            protocol=2,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            max_connections=settings.redis.max_connections,
            health_check_interval=30,
        )
        _clients[purpose] = client
    return client


async def close_redis_clients() -> None:
    """Close every client. Called on application shutdown."""
    for purpose, client in list(_clients.items()):
        # `aclose` is the correct API in redis-py 5+ (`close` is deprecated).
        # It exists at runtime but is missing from the shipped type stubs.
        await client.aclose()  # type: ignore[attr-defined]
        _clients.pop(purpose, None)
    logger.info("redis_clients_closed")


async def take_once(client: Redis[str], key: str) -> str | None:
    """Read a single-use value and delete it, atomically.

    A ``MULTI``/``EXEC`` transaction rather than ``GETDEL``: the supported
    baseline is Redis 3.0.504, and ``GETDEL`` arrived in 6.2. The guarantee is
    the same — Redis runs the queued GET and DEL with no other client's
    command in between, so of two concurrent callers exactly one sees the
    value. Same pattern as the eBay OAuth state and the Google nonce.
    Raises ``RedisError``; callers map it to their own refusal.
    """
    async with client.pipeline(transaction=True) as pipe:
        pipe.get(key)
        pipe.delete(key)
        raw, _ = await pipe.execute()
    if raw is None:
        return None
    return raw if isinstance(raw, str) else bytes(raw).decode("utf-8")


async def check_redis_health() -> bool:
    try:
        return bool(await get_redis(RedisPurpose.CACHE).ping())
    except RedisError as exc:
        logger.warning("redis_health_check_failed", error=str(exc))
        return False


class CacheClient:
    """Thin caching helper with tenant-namespaced keys.

    **Every key is prefixed with the tenant id.** A cache key collision across
    tenants would serve one customer another customer's data, and unlike a
    database query there is no second line of defence. Namespacing is applied
    here rather than left to call sites.

    Cache failures are swallowed on read and logged: an unavailable cache should
    degrade a request to a slower database hit, not fail it. Writes behave the
    same way. Callers that genuinely require Redis — the rate limiter — use the
    raw client instead so they can decide for themselves.
    """

    def __init__(self, client: Redis[str] | None = None) -> None:
        self._client = client or get_redis(RedisPurpose.CACHE)

    @staticmethod
    def _namespaced(key: str, tenant_id: str | None) -> str:
        return f"t:{tenant_id}:{key}" if tenant_id else f"global:{key}"

    async def get(self, key: str, *, tenant_id: str | None = None) -> str | None:
        try:
            return await self._client.get(self._namespaced(key, tenant_id))
        except RedisError as exc:
            logger.warning("cache_read_failed", key=key, error=str(exc))
            return None

    async def set(
        self,
        key: str,
        value: str,
        *,
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        ttl = ttl_seconds if ttl_seconds is not None else settings.redis.default_ttl_seconds
        try:
            await self._client.set(self._namespaced(key, tenant_id), value, ex=ttl)
        except RedisError as exc:
            logger.warning("cache_write_failed", key=key, error=str(exc))
            return False
        return True

    async def delete(self, key: str, *, tenant_id: str | None = None) -> bool:
        try:
            await self._client.delete(self._namespaced(key, tenant_id))
        except RedisError as exc:
            logger.warning("cache_delete_failed", key=key, error=str(exc))
            return False
        return True

    async def invalidate_tenant(self, tenant_id: str) -> int:
        """Delete every cached key for one tenant.

        Uses ``SCAN`` rather than ``KEYS``: ``KEYS`` blocks the single-threaded
        Redis event loop for the duration of a full keyspace walk, which on a
        production instance is an outage.
        """
        pattern = f"t:{tenant_id}:*"
        deleted = 0
        try:
            async for key in self._client.scan_iter(match=pattern, count=500):
                deleted += await self._client.delete(key)
        except RedisError as exc:
            logger.warning("cache_invalidate_failed", tenant_id=tenant_id, error=str(exc))
            raise CacheError("Failed to invalidate tenant cache.") from exc
        return deleted

    async def ping(self) -> Any:
        return await self._client.ping()


__all__ = [
    "CacheClient",
    # Exported so callers that must react to a cache failure rather than
    # degrade — erasure is one — can catch it by name.
    "CacheError",
    "RedisPurpose",
    "check_redis_health",
    "close_redis_clients",
    "get_redis",
    "take_once",
]
