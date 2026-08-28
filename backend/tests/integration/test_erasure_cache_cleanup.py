"""Redis cleanup during erasure: ordering, honesty, and blast radius.

PostgreSQL and Redis cannot commit together. That is not a problem to hide
behind a helper — it is a sequencing decision, and these tests pin the decision:

* the database commits **first**, and Redis is touched only afterwards;
* a rolled-back erasure touches Redis **not at all**;
* a Redis failure after the commit is reported as exactly what it is, rather
  than as a failed erasure or as a clean success;
* a retry finishes the job, including when the database has nothing left to do.

The last one is the case that made this necessary: the accepted runbook claimed
workspace erasure cleared `t:{tenant}:*`, and `invalidate_tenant()` had no
caller at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient, RedisPurpose, get_redis
from app.models.user import User
from app.services.data_subject_erasure import (
    CacheCleanupResult,
    ErasureCacheCleanup,
    execute_platform_user_erasure,
    execute_workspace_closure,
    login_email_key,
    login_ip_key,
)
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def clean_redis_clients() -> AsyncIterator[None]:
    """Do not inherit or leave a Redis client bound to a closed event loop.

    `app.core.redis` caches clients in a module-level dict. This module talks to
    real Redis, so it creates one on its own loop; pytest-asyncio then closes
    that loop and the next module to touch the cached client gets
    `RuntimeError: Event loop is closed` raised inside somebody else's fixture.

    The EBAY-C0 and C0.1 suites already do this for the same reason. Closing on
    both sides means this module neither inherits a stale client nor leaves one.
    """
    from app.core.redis import close_redis_clients

    await close_redis_clients()
    yield
    await close_redis_clients()


async def register(client: AsyncClient, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    response = await client.post("/api/v1/auth/register", json=registration_payload(email=email))
    assert response.status_code == 201, response.text
    identity = response.json()["identity"]["user"]
    return uuid.UUID(identity["id"]), uuid.UUID(identity["tenantId"])


class RecordingCleanup(ErasureCacheCleanup):
    """Records when it ran and what it was asked to do, without touching Redis."""

    def __init__(self, journal: list[str], *, fail: bool = False) -> None:
        self.journal = journal
        self.fail = fail
        self.calls: list[uuid.UUID] = []

    async def invalidate_workspace(self, tenant_id: uuid.UUID) -> CacheCleanupResult:
        self.journal.append("redis")
        self.calls.append(tenant_id)
        if self.fail:
            return CacheCleanupResult(
                attempted=True, succeeded=False, keys_deleted=0, detail="redis unavailable"
            )
        return CacheCleanupResult(
            attempted=True, succeeded=True, keys_deleted=3, detail="tenant cache cleared"
        )

    async def forget_login_attempts(self, email: str) -> CacheCleanupResult:
        self.journal.append("redis")
        if self.fail:
            return CacheCleanupResult(
                attempted=True, succeeded=False, keys_deleted=0, detail="redis unavailable"
            )
        return CacheCleanupResult(
            attempted=True, succeeded=True, keys_deleted=1, detail="login counter cleared"
        )


class TestOrdering:
    async def test_the_database_commits_before_redis_is_touched(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis must never be called while the transaction is still open."""
        _, tenant = await register(client, f"order-{uuid.uuid4().hex}@example.com")
        await db_session.flush()

        journal: list[str] = []
        original_commit = db_session.commit

        async def recording_commit() -> None:
            journal.append("commit")
            await original_commit()

        monkeypatch.setattr(db_session, "commit", recording_commit)
        cleanup = RecordingCleanup(journal)

        await execute_workspace_closure(db_session, tenant, cleanup=cleanup)

        assert journal == ["commit", "redis"], journal

    async def test_a_database_failure_never_reaches_redis(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing was erased, so there is nothing to invalidate."""
        _, tenant = await register(client, f"fail-{uuid.uuid4().hex}@example.com")
        await db_session.flush()

        journal: list[str] = []
        cleanup = RecordingCleanup(journal)

        async def exploding_commit() -> None:
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db_session, "commit", exploding_commit)

        with pytest.raises(RuntimeError, match="commit failed"):
            await execute_workspace_closure(db_session, tenant, cleanup=cleanup)
        await db_session.rollback()

        assert journal == []
        assert cleanup.calls == []


class TestHonestReporting:
    async def test_a_redis_failure_after_commit_is_reported_as_pending(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The erasure happened. Saying it failed would be false."""
        owner, tenant = await register(client, f"pending-{uuid.uuid4().hex}@example.com")
        await db_session.flush()

        execution = await execute_workspace_closure(
            db_session, tenant, cleanup=RecordingCleanup([], fail=True)
        )

        assert execution.cache.pending is True
        assert execution.fully_complete is False
        assert "database erasure complete; cache cleanup pending" in execution.describe()

        # And the database work really did happen.
        row = (await db_session.execute(select(User).where(User.id == owner))).scalar_one()
        assert row.is_active is False
        assert row.email.endswith("@erased.invalid")

    async def test_a_successful_run_is_not_reported_as_pending(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, tenant = await register(client, f"clean-{uuid.uuid4().hex}@example.com")
        await db_session.flush()

        execution = await execute_workspace_closure(
            db_session, tenant, cleanup=RecordingCleanup([])
        )

        assert execution.cache.pending is False
        assert execution.fully_complete is True
        assert "pending" not in execution.describe()


class TestRetry:
    async def test_a_retry_after_a_redis_outage_completes_the_cleanup(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, tenant = await register(client, f"retry-{uuid.uuid4().hex}@example.com")
        await db_session.flush()

        first = await execute_workspace_closure(
            db_session, tenant, cleanup=RecordingCleanup([], fail=True)
        )
        assert first.cache.pending is True

        second = await execute_workspace_closure(db_session, tenant, cleanup=RecordingCleanup([]))
        assert second.cache.pending is False

    async def test_a_zero_count_retry_still_invalidates_redis(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The exact retry case, and the one an early-return would have broken.

        On the second run the database has nothing left to erase. If the cache
        step were skipped when counts are zero, a Redis outage during the first
        run would leave the namespace stale forever.
        """
        _, tenant = await register(client, f"zero-{uuid.uuid4().hex}@example.com")
        await db_session.flush()

        await execute_workspace_closure(db_session, tenant, cleanup=RecordingCleanup([]))

        cleanup = RecordingCleanup([])
        second = await execute_workspace_closure(db_session, tenant, cleanup=cleanup)

        # Nothing left in the database…
        assert second.outcome.counts.get("shopify_connections_deleted", 0) == 0
        assert second.outcome.counts.get("ebay_connections_deleted", 0) == 0
        # …but Redis was still asked.
        assert cleanup.calls == [tenant]
        assert second.cache.attempted is True


class TestBlastRadius:
    async def test_clearing_one_tenant_leaves_another_tenants_keys_alone(self) -> None:
        """Against real Redis: the prefix must not reach a neighbour."""
        cache = CacheClient()
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

        await cache.set("k1", "a-value", tenant_id=str(tenant_a))
        await cache.set("k2", "a-value", tenant_id=str(tenant_a))
        await cache.set("k1", "b-value", tenant_id=str(tenant_b))

        result = await ErasureCacheCleanup(cache=cache).invalidate_workspace(tenant_a)

        assert result.succeeded is True
        assert result.keys_deleted == 2
        assert await cache.get("k1", tenant_id=str(tenant_a)) is None
        assert await cache.get("k2", tenant_id=str(tenant_a)) is None
        # The neighbour is untouched.
        assert await cache.get("k1", tenant_id=str(tenant_b)) == "b-value"

        await cache.invalidate_tenant(str(tenant_b))

    async def test_a_tenant_whose_prefix_is_another_tenants_prefix_is_unaffected(self) -> None:
        """`t:{a}:*` must not match `t:{ab}:...` — the classic prefix bug.

        UUIDs make a genuine collision impossible, so the keys are written
        directly to force the case a naive `startswith` would get wrong.
        """
        client = get_redis(RedisPurpose.CACHE)
        short = "11111111-1111-1111-1111-111111111111"
        longer = f"{short}-extra"

        await client.set(f"t:{short}:key", "short")
        await client.set(f"t:{longer}:key", "longer")

        deleted = await CacheClient().invalidate_tenant(short)

        assert deleted == 1
        assert await client.get(f"t:{short}:key") is None
        assert await client.get(f"t:{longer}:key") == "longer"

        await client.delete(f"t:{longer}:key")


class TestLoginKeyErasure:
    async def test_it_deletes_the_exact_normalised_email_key(self) -> None:
        client = get_redis(RedisPurpose.RATE_LIMIT)
        email = f"exact-{uuid.uuid4().hex}@example.com"
        key = login_email_key(email)

        await client.set(key, "3")
        assert await client.get(key) == "3"

        # Padding and case must resolve to the same key the throttle wrote.
        result = await ErasureCacheCleanup(throttle_client=client).forget_login_attempts(
            f"  {email.upper()}  "
        )

        assert result.succeeded is True
        assert result.keys_deleted == 1
        assert await client.get(key) is None

    async def test_it_leaves_the_ip_counter_alone(self) -> None:
        """An address is not a person; erasing "their" IP counter is a guess."""
        client = get_redis(RedisPurpose.RATE_LIMIT)
        email = f"ipkeep-{uuid.uuid4().hex}@example.com"
        ip_key = login_ip_key("203.0.113.9")

        await client.set(login_email_key(email), "2")
        await client.set(ip_key, "5")

        await ErasureCacheCleanup(throttle_client=client).forget_login_attempts(email)

        assert await client.get(ip_key) == "5"
        await client.delete(ip_key)

    async def test_erasing_by_user_id_reports_the_login_key_as_not_attempted(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """No address means no key to derive; the counter expires on its own."""
        from app.services.data_subject_erasure import PlatformUserErasureService

        email = f"noaddr-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)
        await db_session.flush()

        subject = await PlatformUserErasureService(db_session).resolve_by_id(
            tenant_id=tenant, user_id=user
        )
        execution = await execute_platform_user_erasure(db_session, subject, email=None)

        assert execution.cache.attempted is False
        assert execution.cache.pending is False
        assert "expire" in execution.cache.detail

    def test_the_ip_key_format_matches_the_throttle(self) -> None:
        """The runbook previously described this key wrongly."""
        key = login_ip_key("203.0.113.9")

        assert key.startswith("login:ip:")
        assert "203.0.113.9" not in key
        assert len(key.split(":")[2]) == 32


class TestForbiddenCommands:
    def test_no_erasure_path_calls_flushdb_flushall_or_keys(self) -> None:
        """`FLUSHDB` clears every tenant to clean up one. `KEYS` blocks Redis.

        Matches *calls*, not mentions — the modules deliberately say "never
        FLUSHDB" in their own documentation, and a substring check would fail on
        the very comment that promises the behaviour.
        """
        import pathlib
        import re

        forbidden = re.compile(r"\.\s*(flushdb|flushall|keys)\s*\(", re.IGNORECASE)

        for name in (
            "app/services/data_subject_erasure.py",
            "app/core/redis.py",
            "scripts/erase_data_subject.py",
        ):
            source = pathlib.Path(name).read_text(encoding="utf-8")
            found = forbidden.findall(source)
            assert found == [], f"{name} calls {found}"

    def test_invalidation_uses_only_commands_redis_3_0_supports(self) -> None:
        """The deployed Redis is 3.0.504 — SCAN is 2.8, UNLINK and GETDEL are not."""
        import inspect

        source = inspect.getsource(CacheClient.invalidate_tenant).lower()
        assert "scan_iter" in source
        for newer in ("unlink", "getdel", "hello"):
            assert newer not in source, f"invalidate_tenant uses {newer}, unavailable on 3.0.504"


class TestNoLeakage:
    async def test_cleanup_results_carry_no_address_or_key(self) -> None:
        client = get_redis(RedisPurpose.RATE_LIMIT)
        email = f"leak-{uuid.uuid4().hex}@example.com"
        await client.set(login_email_key(email), "1")

        result = await ErasureCacheCleanup(throttle_client=client).forget_login_attempts(email)

        rendered = f"{result.detail} {result!r}"
        assert email not in rendered
        assert "@" not in result.detail
        # The key is a hash of a low-entropy value; publishing it builds a
        # lookup table for whoever collects the logs.
        assert login_email_key(email) not in rendered

    async def test_a_redis_failure_detail_names_the_error_type_only(self) -> None:
        class ExplodingClient:
            async def delete(self, *keys: str) -> int:
                raise RedisConnectionError("connection refused to 10.0.0.5:6379")

        cleanup = ErasureCacheCleanup(throttle_client=ExplodingClient())
        result = await cleanup.forget_login_attempts("someone@example.com")

        assert result.succeeded is False
        assert "ConnectionError" in result.detail
        # Not the host, not the address, not the key.
        assert "10.0.0.5" not in result.detail
        assert "someone@example.com" not in result.detail

    async def test_workspace_failure_detail_names_the_error_type_only(self) -> None:
        class ExplodingCache:
            async def invalidate_tenant(self, tenant_id: str) -> int:
                raise RedisConnectionError("connection refused to 10.0.0.5:6379")

        cleanup = ErasureCacheCleanup(cache=cast_any(ExplodingCache()))
        result = await cleanup.invalidate_workspace(uuid.uuid4())

        assert result.succeeded is False
        assert result.pending is True
        assert "10.0.0.5" not in result.detail


def cast_any(value: object) -> Any:
    """Small shim so a stub can stand in for `CacheClient` under strict typing."""
    return value
