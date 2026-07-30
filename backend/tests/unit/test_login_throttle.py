"""Tests for login attempt throttling.

**This file closes a real gap.** Until it existed, the throttle had no coverage
at all: the integration suite disables it for every test, and a fixture
docstring wrongly claimed a dedicated test existed. The throttle is the primary
defence against credential stuffing on the platform's most exposed endpoint, so
a regression in it was previously invisible to CI.

These run against ``fakeredis`` rather than a live server. That is deliberate:
requiring real Redis would mean the tests skip on any machine without one, which
is precisely how a security control ends up untested. The Lua-free code paths
used here (``GET``, ``INCR``, ``EXPIRE``, ``TTL``, ``DELETE``) are faithfully
emulated.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fakeredis import aioredis as fake_aioredis

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError
from app.services import login_throttle as throttle_module
from app.services.login_throttle import LoginThrottle

pytestmark = pytest.mark.unit

EMAIL = "victim@example.com"
OTHER_EMAIL = "someone-else@example.com"
IP = "203.0.113.10"
OTHER_IP = "203.0.113.99"


@pytest.fixture
async def fake_redis() -> AsyncGenerator[fake_aioredis.FakeRedis]:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def throttle(monkeypatch: pytest.MonkeyPatch, fake_redis: fake_aioredis.FakeRedis) -> LoginThrottle:
    """A throttle wired to the in-process Redis.

    ``get_redis`` is resolved at call time inside each method, so patching the
    name in the throttle's own module is sufficient and does not disturb the
    real client used elsewhere.
    """
    monkeypatch.setattr(throttle_module, "get_redis", lambda _purpose: fake_redis)
    monkeypatch.setattr(settings.security, "rate_limit_enabled", True)
    return LoginThrottle()


async def fail_n_times(
    throttle: LoginThrottle, n: int, *, email: str = EMAIL, ip: str | None = IP
) -> None:
    for _ in range(n):
        await throttle.record_failure(email=email, client_ip=ip)


class TestThresholds:
    async def test_allows_attempts_below_the_limit(self, throttle: LoginThrottle) -> None:
        await fail_n_times(throttle, settings.security.login_max_attempts - 1)
        # Must not raise.
        await throttle.check(email=EMAIL, client_ip=IP)

    async def test_blocks_once_the_limit_is_reached(self, throttle: LoginThrottle) -> None:
        await fail_n_times(throttle, settings.security.login_max_attempts)

        with pytest.raises(RateLimitExceededError):
            await throttle.check(email=EMAIL, client_ip=IP)

    async def test_stays_blocked_beyond_the_limit(self, throttle: LoginThrottle) -> None:
        await fail_n_times(throttle, settings.security.login_max_attempts + 5)

        with pytest.raises(RateLimitExceededError):
            await throttle.check(email=EMAIL, client_ip=IP)

    async def test_reports_a_retry_after_hint(self, throttle: LoginThrottle) -> None:
        """The client needs to know how long to wait, not just that it failed."""
        await fail_n_times(throttle, settings.security.login_max_attempts)

        with pytest.raises(RateLimitExceededError) as exc_info:
            await throttle.check(email=EMAIL, client_ip=IP)

        assert exc_info.value.retry_after_seconds is not None
        assert exc_info.value.retry_after_seconds > 0

    async def test_a_fresh_identity_is_unaffected(self, throttle: LoginThrottle) -> None:
        await fail_n_times(throttle, settings.security.login_max_attempts)

        await throttle.check(email=OTHER_EMAIL, client_ip=OTHER_IP)


class TestDimensionIndependence:
    """Email and IP are counted separately, and either alone can block.

    Both dimensions exist because neither is sufficient:

    * IP alone lets an attacker spread one password across thousands of accounts
      from one address without tripping a per-account limit.
    * Email alone lets an attacker lock a known user out of their own account by
      deliberately failing their login — denial of service dressed as security.
    """

    async def test_email_dimension_blocks_across_changing_ips(
        self, throttle: LoginThrottle
    ) -> None:
        """A distributed attack on one account must still be stopped."""
        for index in range(settings.security.login_max_attempts):
            await throttle.record_failure(email=EMAIL, client_ip=f"198.51.100.{index}")

        with pytest.raises(RateLimitExceededError):
            await throttle.check(email=EMAIL, client_ip="198.51.100.200")

    async def test_ip_dimension_blocks_across_changing_emails(
        self, throttle: LoginThrottle
    ) -> None:
        """Credential stuffing — many accounts, one source — must be stopped."""
        for index in range(settings.security.login_max_attempts):
            await throttle.record_failure(email=f"user{index}@example.com", client_ip=IP)

        with pytest.raises(RateLimitExceededError):
            await throttle.check(email="fresh-account@example.com", client_ip=IP)

    async def test_a_missing_ip_still_counts_the_email(self, throttle: LoginThrottle) -> None:
        """A caller with no resolvable IP must not escape throttling entirely."""
        await fail_n_times(throttle, settings.security.login_max_attempts, ip=None)

        with pytest.raises(RateLimitExceededError):
            await throttle.check(email=EMAIL, client_ip=None)


class TestClearing:
    async def test_success_clears_both_counters(self, throttle: LoginThrottle) -> None:
        """A user who mistypes twice then succeeds starts fresh.

        Without this, ordinary typos accumulate towards a lockout across a
        working day.
        """
        await fail_n_times(throttle, settings.security.login_max_attempts - 1)
        await throttle.clear(email=EMAIL, client_ip=IP)

        await fail_n_times(throttle, settings.security.login_max_attempts - 1)
        await throttle.check(email=EMAIL, client_ip=IP)

    async def test_clearing_an_untouched_identity_is_harmless(
        self, throttle: LoginThrottle
    ) -> None:
        await throttle.clear(email=OTHER_EMAIL, client_ip=OTHER_IP)


class TestExpiry:
    async def test_the_first_failure_sets_a_window(
        self, throttle: LoginThrottle, fake_redis: fake_aioredis.FakeRedis
    ) -> None:
        """A counter without a TTL would lock an account out permanently."""
        await throttle.record_failure(email=EMAIL, client_ip=IP)

        keys = await fake_redis.keys("login:*")
        assert keys
        for key in keys:
            assert await fake_redis.ttl(key) > 0

    async def test_reaching_the_limit_extends_to_the_lockout_window(
        self, throttle: LoginThrottle, fake_redis: fake_aioredis.FakeRedis
    ) -> None:
        """Otherwise an attacker waits out the short window and resumes."""

        async def max_ttl() -> int:
            # A generator expression cannot contain `await` in this position,
            # so the TTLs are collected explicitly.
            ttls = [await fake_redis.ttl(key) for key in await fake_redis.keys("login:*")]
            return max(ttls)

        await throttle.record_failure(email=EMAIL, client_ip=IP)
        short_window_ttl = await max_ttl()

        await fail_n_times(throttle, settings.security.login_max_attempts - 1)
        lockout_ttl = await max_ttl()

        assert lockout_ttl > short_window_ttl


class TestPrivacy:
    async def test_email_addresses_are_not_stored_in_redis_keys(
        self, throttle: LoginThrottle, fake_redis: fake_aioredis.FakeRedis
    ) -> None:
        """Keys appear in MONITOR output, slow logs, and support dumps.

        A keyspace full of customer email addresses is a personal-data leak
        waiting to be exported.
        """
        await throttle.record_failure(email=EMAIL, client_ip=IP)

        keys = await fake_redis.keys("login:*")
        assert keys
        for key in keys:
            assert EMAIL not in key
            assert "victim" not in key

    async def test_ip_addresses_are_not_stored_in_redis_keys(
        self, throttle: LoginThrottle, fake_redis: fake_aioredis.FakeRedis
    ) -> None:
        await throttle.record_failure(email=EMAIL, client_ip=IP)

        for key in await fake_redis.keys("login:*"):
            assert IP not in key


class TestFailureModes:
    async def test_fails_open_when_redis_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An infrastructure outage must not lock every customer out.

        A deliberate availability-over-enforcement trade: while Redis is down
        there is no throttling at all, which is why Redis needs its own
        alerting. Documented in docs/Authentication.md.
        """
        from redis.exceptions import ConnectionError as RedisConnectionError

        class BrokenRedis:
            async def get(self, *_args: object, **_kwargs: object) -> object:
                raise RedisConnectionError("redis is down")

            async def incr(self, *_args: object, **_kwargs: object) -> object:
                raise RedisConnectionError("redis is down")

        monkeypatch.setattr(throttle_module, "get_redis", lambda _purpose: BrokenRedis())
        monkeypatch.setattr(settings.security, "rate_limit_enabled", True)
        broken = LoginThrottle()

        # Neither call may raise.
        await broken.record_failure(email=EMAIL, client_ip=IP)
        await broken.check(email=EMAIL, client_ip=IP)

    async def test_disabled_throttle_is_a_no_op(
        self, monkeypatch: pytest.MonkeyPatch, fake_redis: fake_aioredis.FakeRedis
    ) -> None:
        monkeypatch.setattr(throttle_module, "get_redis", lambda _purpose: fake_redis)
        monkeypatch.setattr(settings.security, "rate_limit_enabled", False)
        disabled = LoginThrottle()

        await fail_n_times(disabled, settings.security.login_max_attempts * 3)
        await disabled.check(email=EMAIL, client_ip=IP)

        assert await fake_redis.keys("login:*") == []
