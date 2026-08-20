"""M3A acceptance fix — named endpoint quotas are per tenant *and* per user.

The quota was keyed on the tenant alone. On a shared workspace that means any
one seat can spend the whole account's allowance: a colleague leaving a
preview open, or a script someone wrote, locks every other member out of the
same endpoint. A limiter that turns one member into a denial of service for
the rest is not doing the job it was added for.

These drive the real endpoints through the real dependency: the key comes from
``endpoint_rate_limit``, the decision from ``FixedWindowLimiter``, and the 429
and ``Retry-After`` from the ordinary error envelope. Only the Redis socket is
substituted.
"""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import AsyncGenerator, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit as rate_limit_module
from app.core.config import settings
from app.core.tokens import create_access_token
from tests.integration.test_rule_application import (
    APPLY,
    BASE,
    IMPACT,
    create_rule,
    seed_tenant,
)

pytestmark = pytest.mark.integration

PREVIEW = f"{BASE}/preview"

#: Matches the quota declared on `POST /global-rules/drafts/apply`. Asserted
#: below rather than only used, so a change to one without the other fails
#: here instead of quietly making this test meaningless.
APPLY_QUOTA = 20


class ScriptedRedis:
    """The narrowest Redis that can run the limiter's one script.

    ``fakeredis`` is what the rest of the suite uses, but executing a Lua
    script needs its optional ``lupa`` extra, which this project does not
    depend on. Rather than add a dependency for one test module, this
    implements exactly the contract the limiter relies on — increment, set the
    TTL on first use, return ``{count, ttl}`` — so the key construction, the
    window arithmetic, the decision and the error response all still come from
    production code. Only the storage is local.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def script_load(self, _script: str) -> str:
        return "sha"

    async def evalsha(self, _sha: str, _numkeys: int, key: str, window: str) -> list[int]:
        count = self.counts.get(key, 0) + 1
        self.counts[key] = count
        if count == 1:
            self.ttls[key] = int(window)
        return [count, self.ttls.get(key, int(window))]

    async def keys(self, pattern: str) -> list[str]:
        return [key for key in self.counts if fnmatch.fnmatchcase(key, pattern)]


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScriptedRedis]:
    client = ScriptedRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis", lambda _purpose: client)
    # The limiter is a process-wide singleton holding a cached script SHA from
    # whichever Redis it last spoke to. A fresh fake has an empty script cache,
    # so the cached SHA has to go with it.
    monkeypatch.setattr(rate_limit_module.limiter, "_script_sha", None)
    monkeypatch.setattr(rate_limit_module.limiter, "_consecutive_failures", 0)
    monkeypatch.setattr(rate_limit_module.limiter, "_circuit_open_until", 0.0)
    yield client


@pytest.fixture
async def throttled(redis: ScriptedRedis) -> AsyncGenerator[None]:
    """Turn endpoint limiting back on for this module.

    The integration conftest disables it for every other test, which is right:
    a suite that fires hundreds of requests from one address would otherwise
    throttle itself. Here it is the subject.
    """
    original = settings.security.rate_limit_enabled
    settings.security.rate_limit_enabled = True
    yield
    settings.security.rate_limit_enabled = original


def token_for(*, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, str]:
    """A signed identity. The limiter reads only from verified claims."""
    issued = create_access_token(user_id=user_id, tenant_id=tenant_id, roles=("owner",))
    return {"Authorization": f"Bearer {issued.token}"}


async def exhaust_apply(client: AsyncClient, headers: dict[str, str]) -> None:
    """Spend one identity's whole apply quota without doing any work.

    The empty selection is refused by the service, so no application row is
    created -- but the limiter has already counted the request, because it
    runs as a dependency rather than inside the handler.
    """
    for index in range(APPLY_QUOTA):
        response = await client.post(
            APPLY,
            json={"productIds": [], "idempotencyKey": f"quota-{index}"},
            headers=headers,
        )
        assert response.status_code != 429, f"throttled early at {index}"


class TestPerUserQuotas:
    async def test_one_member_cannot_spend_the_whole_workspace_allowance(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        throttled: None,
    ) -> None:
        """The defect this fix exists for, stated as behaviour."""
        headers, tenant_id = await seed_tenant(client)
        colleague = token_for(tenant_id=tenant_id, user_id=uuid.uuid4())

        await exhaust_apply(client, headers)

        blocked = await client.post(
            APPLY, json={"productIds": [], "idempotencyKey": "one-too-many"}, headers=headers
        )
        assert blocked.status_code == 429, blocked.text
        assert blocked.headers.get("Retry-After") is not None
        assert int(blocked.headers["Retry-After"]) > 0

        # The colleague, in the same workspace, is unaffected.
        allowed = await client.post(
            APPLY, json={"productIds": [], "idempotencyKey": "colleague"}, headers=colleague
        )
        assert allowed.status_code != 429, allowed.text

    async def test_the_same_user_identifier_in_another_tenant_is_a_different_bucket(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        throttled: None,
        redis: ScriptedRedis,
    ) -> None:
        """User ids are only unique within a tenant here, so the tenant half of
        the key is what keeps two workspaces apart."""
        _, first_tenant = await seed_tenant(client)
        _, second_tenant = await seed_tenant(client)
        shared_user = uuid.uuid4()
        first = token_for(tenant_id=first_tenant, user_id=shared_user)
        second = token_for(tenant_id=second_tenant, user_id=shared_user)

        await exhaust_apply(client, first)

        blocked = await client.post(
            APPLY, json={"productIds": [], "idempotencyKey": "blocked"}, headers=first
        )
        assert blocked.status_code == 429

        allowed = await client.post(
            APPLY, json={"productIds": [], "idempotencyKey": "other-tenant"}, headers=second
        )
        assert allowed.status_code != 429

        keys = await redis.keys("ratelimit:global-rules-apply:*")
        assert f"ratelimit:global-rules-apply:tenant:{first_tenant}:user:{shared_user}" in keys
        assert f"ratelimit:global-rules-apply:tenant:{second_tenant}:user:{shared_user}" in keys

    async def test_two_members_of_one_tenant_get_two_counters(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        throttled: None,
        redis: ScriptedRedis,
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        colleague_id = uuid.uuid4()
        colleague = token_for(tenant_id=tenant_id, user_id=colleague_id)

        await client.post(APPLY, json={"productIds": [], "idempotencyKey": "a"}, headers=headers)
        await client.post(APPLY, json={"productIds": [], "idempotencyKey": "b"}, headers=colleague)

        keys = await redis.keys(f"ratelimit:global-rules-apply:tenant:{tenant_id}:user:*")
        assert len(keys) == 2, keys
        assert any(str(colleague_id) in key for key in keys)


class TestQuotaNames:
    async def test_preview_impact_and_apply_are_counted_separately(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        throttled: None,
        redis: ScriptedRedis,
    ) -> None:
        """One expensive endpoint must not consume another's allowance."""
        headers, _tenant_id = await seed_tenant(client)
        await create_rule(client, headers)

        await client.post(
            PREVIEW,
            json={"itemCost": "10.00", "shippingCost": "4.00", "currency": "USD"},
            headers=headers,
        )
        await client.get(IMPACT, headers=headers)
        await client.post(APPLY, json={"productIds": [], "idempotencyKey": "x"}, headers=headers)

        names = {key.split(":")[1] for key in await redis.keys("ratelimit:*")}
        assert {
            "global-rules-preview",
            "global-rules-impact",
            "global-rules-apply",
        } <= names, names


class TestIdentityCannotBeChosenByTheCaller:
    async def test_an_unauthenticated_request_gets_no_tenant_bucket(
        self,
        client: AsyncClient,
        throttled: None,
        redis: ScriptedRedis,
    ) -> None:
        """Authentication resolves first, structurally: the dependency asks for
        the principal, so FastAPI has to produce one before it runs."""
        response = await client.get(IMPACT)
        assert response.status_code == 401, response.text

        keys = await redis.keys("ratelimit:global-rules-impact:*")
        assert not any("tenant:" in key for key in keys), keys

    async def test_a_forged_tenant_header_does_not_choose_the_bucket(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        throttled: None,
        redis: ScriptedRedis,
    ) -> None:
        """Phase 1 removed `X-Tenant-ID` as an identity source. Sending one must
        not reintroduce it through the limiter's key."""
        headers, tenant_id = await seed_tenant(client)
        victim = uuid.uuid4()

        await client.get(
            IMPACT,
            headers={**headers, "X-Tenant-ID": str(victim), "X-User-ID": str(victim)},
        )

        keys = await redis.keys("ratelimit:global-rules-impact:*")
        assert keys, "the request was counted"
        assert all(str(victim) not in key for key in keys), keys
        assert any(str(tenant_id) in key for key in keys), keys


class TestRedisOutagePolicy:
    async def test_a_redis_failure_lets_the_request_through(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        throttled: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The documented trade: a cache outage degrades to "no quota" rather
        than taking pricing down with it. Unchanged by this fix, and asserted
        so it stays that way."""
        from redis.exceptions import RedisError

        headers, _ = await seed_tenant(client)

        class Broken:
            async def script_load(self, _script: str) -> str:
                raise RedisError("down")

            async def evalsha(self, *_args: object) -> object:
                raise RedisError("down")

        monkeypatch.setattr(rate_limit_module, "get_redis", lambda _purpose: Broken())

        response = await client.get(IMPACT, headers=headers)
        assert response.status_code == 200, response.text
