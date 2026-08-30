"""AUTH-G1-R3: the two Redis defects integration review found, as controls.

Both are about state that outlives a request, which is why neither showed up in
a suite that only ever exercised whole successful and whole failed attempts:

* the counter and its expiry were two round trips, so an interruption between
  them left an immortal key and a user locked out of linking for good;
* a success deleted the *shared* address counter, so anybody with one ordinary
  account could wipe the failed attempts of every other account behind the same
  address simply by succeeding on their own.

Everything runs against the real Redis 3.0.504 the deployment uses, so `EVAL`
and the script's arithmetic are exercised on the version that will run them.
Nothing here contacts Google or sends mail.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError
from app.core.redis import RedisPurpose, get_redis
from app.services.login_throttle import StepUpThrottle, StepUpUnavailableError

pytestmark = pytest.mark.integration

CONCURRENCY = 20


@pytest.fixture(autouse=True)
async def clean_redis_clients() -> AsyncIterator[None]:
    from app.core.redis import close_redis_clients

    await close_redis_clients()
    yield
    await close_redis_clients()


@pytest.fixture
def throttling_enabled() -> AsyncIterator[None]:
    """Re-enable rate limiting, which the integration conftest turns off."""
    original = settings.security.rate_limit_enabled
    settings.security.rate_limit_enabled = True
    yield
    settings.security.rate_limit_enabled = original


def fresh_ip() -> str:
    """An address no earlier test or run has counted against.

    Counters outlive a test — they expire on a window, not on teardown — so a
    fixed address makes these pass or fail depending on what ran in the last
    five minutes.
    """
    raw = uuid.uuid4().int
    return f"100.64.{(raw >> 8) & 0x3F}.{raw & 0xFF}"


async def redis_client() -> Any:
    return get_redis(RedisPurpose.RATE_LIMIT)


# ---------------------------------------------------------------------------
# Defect 1 — the counter and its expiry must be indivisible
# ---------------------------------------------------------------------------


class TestCounterAndExpiryAreAtomic:
    """`INCR` then `EXPIRE` is two round trips and one window to lose.

    Lose it and the key has no expiry, which is not a slightly-wrong counter —
    it is an account that can never link or unlink a sign-in method again until
    somebody finds and deletes the key by hand.
    """

    async def test_a_first_attempt_always_leaves_a_positive_ttl(
        self, throttling_enabled: None
    ) -> None:
        throttle = StepUpThrottle()
        user_id, tenant_id, ip = uuid.uuid4(), uuid.uuid4(), fresh_ip()
        client = await redis_client()

        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=ip)

        for key in (
            throttle.user_key(user_id=user_id, tenant_id=tenant_id),
            throttle.address_key(ip),
        ):
            ttl = int(await client.ttl(key))
            assert ttl > 0, f"{key} has TTL {ttl}"
            assert ttl <= settings.security.step_up_attempt_window_seconds

    async def test_no_reserve_at_any_depth_leaves_an_immortal_key(
        self, throttling_enabled: None
    ) -> None:
        """Checked after *every* attempt, not only the first.

        The old code assigned an expiry on the first attempt and again when the
        ceiling was crossed, and at no other time — so a key created by a lost
        `EXPIRE` stayed immortal through every attempt in between.
        """
        throttle = StepUpThrottle()
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        client = await redis_client()
        key = throttle.user_key(user_id=user_id, tenant_id=tenant_id)

        for attempt in range(settings.security.step_up_max_attempts + 4):
            try:
                await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
            except RateLimitExceededError:
                pass
            ttl = int(await client.ttl(key))
            assert ttl > 0, f"TTL {ttl} after attempt {attempt + 1}"

    async def test_an_interrupted_reserve_cannot_create_a_counter_without_expiry(
        self, throttling_enabled: None
    ) -> None:
        """Fault injection: kill the connection mid-operation, repeatedly.

        With two round trips there is a window where the counter exists and the
        expiry does not, and an interruption lands in it. With one script there
        is no such window: Redis either ran the whole thing or none of it.
        """
        throttle = StepUpThrottle()
        client = await redis_client()
        survivors: list[tuple[str, int]] = []

        for _ in range(40):
            user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
            key = throttle.user_key(user_id=user_id, tenant_id=tenant_id)

            # Cancellation is the sharpest form of the fault: it can land
            # between any two awaits inside `reserve`, which is precisely where
            # the old INCR/EXPIRE pair was vulnerable.
            task = asyncio.create_task(
                throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
            )
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RateLimitExceededError, StepUpUnavailableError):
                pass

            if await client.exists(key):
                survivors.append((key, int(await client.ttl(key))))

        immortal = [(key, ttl) for key, ttl in survivors if ttl < 0]
        assert not immortal, f"{len(immortal)} counter(s) left with no expiry: {immortal[:3]}"

    async def test_a_legacy_key_without_an_expiry_self_heals(
        self, throttling_enabled: None
    ) -> None:
        """Keys left behind by the previous version must not stay immortal.

        Deployments will have them: the old code could create one, and nothing
        removes it. The script repairs any key it finds without an expiry.
        """
        throttle = StepUpThrottle()
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        client = await redis_client()
        key = throttle.user_key(user_id=user_id, tenant_id=tenant_id)

        # Exactly the state the old non-atomic pair could leave behind.
        await client.incr(key)
        assert int(await client.ttl(key)) == -1

        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)

        assert int(await client.ttl(key)) > 0

    async def test_the_lockout_is_bounded_and_never_extended(
        self, throttling_enabled: None
    ) -> None:
        """A lockout an attacker can keep extending is a denial of service.

        The previous version re-applied the lockout TTL on *every* attempt past
        the ceiling, so somebody holding a stolen session could keep the real
        account holder locked out of their own settings indefinitely by
        continuing to guess. The expiry is now assigned once, on the attempt
        that crosses the ceiling, and never touched again.
        """
        throttle = StepUpThrottle()
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        client = await redis_client()
        key = throttle.user_key(user_id=user_id, tenant_id=tenant_id)

        for _ in range(settings.security.step_up_max_attempts + 1):
            try:
                await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
            except RateLimitExceededError:
                pass

        at_lockout = int(await client.ttl(key))
        assert at_lockout > settings.security.step_up_attempt_window_seconds, (
            f"the ceiling did not extend the window: TTL {at_lockout}"
        )

        # Twenty more attempts must not push the expiry back out.
        for _ in range(20):
            try:
                await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
            except RateLimitExceededError:
                pass

        after = int(await client.ttl(key))
        assert after <= at_lockout, f"the lockout was extended from {at_lockout} to {after}"

    async def test_the_window_is_fixed_rather_than_sliding(self, throttling_enabled: None) -> None:
        """Attempts below the ceiling must not push the window out either."""
        throttle = StepUpThrottle()
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        client = await redis_client()
        key = throttle.user_key(user_id=user_id, tenant_id=tenant_id)

        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
        first = int(await client.ttl(key))
        await client.expire(key, first - 60)  # simulate the window draining
        drained = int(await client.ttl(key))

        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)

        assert int(await client.ttl(key)) <= drained, "a later attempt refreshed the window"

    async def test_the_two_dimensions_stay_independent(self, throttling_enabled: None) -> None:
        """One script call per key, so neither can be counted into the other."""
        throttle = StepUpThrottle()
        client = await redis_client()
        user_id, tenant_id, ip = uuid.uuid4(), uuid.uuid4(), fresh_ip()

        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=ip)
        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)

        user_count = int(await client.get(throttle.user_key(user_id=user_id, tenant_id=tenant_id)))
        address_count = int(await client.get(throttle.address_key(ip)))

        assert user_count == 2
        assert address_count == 1, "the address dimension counted an attempt it never saw"

    async def test_concurrent_attempts_gain_no_extra_allowance(
        self, throttling_enabled: None
    ) -> None:
        """The ceiling has to survive the script change, not just the old code."""
        throttle = StepUpThrottle()
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()

        results = await asyncio.gather(
            *(
                throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
                for _ in range(CONCURRENCY)
            ),
            return_exceptions=True,
        )

        admitted = sum(1 for r in results if not isinstance(r, BaseException))
        assert admitted == settings.security.step_up_max_attempts, (
            f"{admitted} of {CONCURRENCY} concurrent attempts were admitted"
        )

    async def test_a_redis_outage_still_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch, throttling_enabled: None
    ) -> None:
        """The script must not have turned a refusal into a pass."""
        from app.services import login_throttle as module

        def unavailable(_purpose: object) -> Any:
            raise RedisError("connection refused")

        monkeypatch.setattr(module, "get_redis", unavailable)

        with pytest.raises(StepUpUnavailableError):
            await StepUpThrottle().reserve(
                user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), client_ip=fresh_ip()
            )


# ---------------------------------------------------------------------------
# Defect 2 — a success must not erase shared history
# ---------------------------------------------------------------------------


class TestSuccessDoesNotEraseSharedHistory:
    """The shared budget belongs to the address, not to whoever succeeded last.

    An attacker needs only one ordinary account of their own to exploit the old
    behaviour: guess twice against a victim, succeed once on their own account,
    and the address counter is back to zero. Repeat forever.
    """

    async def test_a_success_leaves_another_accounts_failures_on_the_shared_counter(
        self, throttling_enabled: None
    ) -> None:
        throttle = StepUpThrottle()
        shared = fresh_ip()
        client = await redis_client()
        address = throttle.address_key(shared)

        # Account B accumulates failures from this address.
        b_user, b_tenant = uuid.uuid4(), uuid.uuid4()
        for _ in range(2):
            await throttle.reserve(user_id=b_user, tenant_id=b_tenant, client_ip=shared)
        before = int(await client.get(address))
        assert before == 2

        # Account A succeeds from the same address.
        a_user, a_tenant = uuid.uuid4(), uuid.uuid4()
        await throttle.reserve(user_id=a_user, tenant_id=a_tenant, client_ip=shared)
        await throttle.clear_user_attempts(user_id=a_user, tenant_id=a_tenant)

        after = await client.get(address)
        assert after is not None, "the shared address counter was deleted by a success"
        assert int(after) == before + 1, (
            "the shared counter should keep B's two failures and A's own attempt"
        )

    async def test_a_success_clears_only_that_accounts_own_counter(
        self, throttling_enabled: None
    ) -> None:
        throttle = StepUpThrottle()
        shared = fresh_ip()
        client = await redis_client()

        a_user, a_tenant = uuid.uuid4(), uuid.uuid4()
        b_user, b_tenant = uuid.uuid4(), uuid.uuid4()

        await throttle.reserve(user_id=a_user, tenant_id=a_tenant, client_ip=shared)
        await throttle.reserve(user_id=b_user, tenant_id=b_tenant, client_ip=shared)

        await throttle.clear_user_attempts(user_id=a_user, tenant_id=a_tenant)

        assert await client.get(throttle.user_key(user_id=a_user, tenant_id=a_tenant)) is None
        assert await client.get(throttle.user_key(user_id=b_user, tenant_id=b_tenant)) is not None

    async def test_no_method_can_clear_the_address_dimension(self) -> None:
        """The split is structural, not a convention somebody must remember.

        `clear()` took a `client_ip` and deleted both keys, so a future caller
        could reintroduce the defect by passing the argument it invited. The
        replacement has no parameter that could reach the shared counter.
        """
        import inspect

        throttle = StepUpThrottle()
        assert not hasattr(throttle, "clear"), "the both-dimension clear() still exists"

        signature = inspect.signature(throttle.clear_user_attempts)
        assert "client_ip" not in signature.parameters

        # And nothing else in the service deletes an address key.
        source = inspect.getsource(type(throttle))
        delete_calls = [line for line in source.splitlines() if ".delete(" in line]
        assert len(delete_calls) == 1, delete_calls
        assert "user_key" in delete_calls[0], delete_calls[0]

    async def test_shared_history_drains_only_through_its_ttl(
        self, throttling_enabled: None
    ) -> None:
        throttle = StepUpThrottle()
        shared = fresh_ip()
        client = await redis_client()
        address = throttle.address_key(shared)

        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=shared)
        await throttle.clear_user_attempts(user_id=user_id, tenant_id=tenant_id)

        ttl = int(await client.ttl(address))
        assert ttl > 0, f"the shared counter has TTL {ttl} and would never drain"
        assert ttl <= settings.security.step_up_lockout_seconds

    async def test_a_success_does_not_hand_the_address_extra_attempts(
        self, throttling_enabled: None
    ) -> None:
        """The whole point: succeeding must not buy back the shared budget."""
        throttle = StepUpThrottle()
        shared = fresh_ip()
        budget = settings.security.step_up_max_attempts_per_ip

        spent = 0
        while spent < budget:
            user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
            await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=shared)
            spent += 1
            # Every one of these is a *success*, which under the old cleanup
            # would have reset the shared counter each time.
            await throttle.clear_user_attempts(user_id=user_id, tenant_id=tenant_id)

        with pytest.raises(RateLimitExceededError):
            await throttle.reserve(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), client_ip=shared)

    async def test_concurrent_success_and_failure_erase_nothing(
        self, throttling_enabled: None
    ) -> None:
        """A success landing mid-burst must not take the burst with it."""
        throttle = StepUpThrottle()
        shared = fresh_ip()
        client = await redis_client()
        victim, victim_tenant = uuid.uuid4(), uuid.uuid4()
        winner, winner_tenant = uuid.uuid4(), uuid.uuid4()

        async def failing() -> None:
            try:
                await throttle.reserve(user_id=victim, tenant_id=victim_tenant, client_ip=shared)
            except RateLimitExceededError:
                pass

        async def succeeding() -> None:
            try:
                await throttle.reserve(user_id=winner, tenant_id=winner_tenant, client_ip=shared)
            except RateLimitExceededError:
                pass
            await throttle.clear_user_attempts(user_id=winner, tenant_id=winner_tenant)

        await asyncio.gather(*(failing() for _ in range(6)), *(succeeding() for _ in range(4)))

        address_count = int(await client.get(throttle.address_key(shared)) or 0)
        assert address_count == 10, (
            f"the shared counter recorded {address_count} of 10 concurrent attempts"
        )

    async def test_another_tenant_cannot_touch_either_counter(
        self, throttling_enabled: None
    ) -> None:
        throttle = StepUpThrottle()
        shared = fresh_ip()
        client = await redis_client()
        user_id = uuid.uuid4()
        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

        await throttle.reserve(user_id=user_id, tenant_id=tenant_a, client_ip=shared)
        await throttle.clear_user_attempts(user_id=user_id, tenant_id=tenant_b)

        assert await client.get(throttle.user_key(user_id=user_id, tenant_id=tenant_a)) is not None
        assert await client.get(throttle.address_key(shared)) is not None

    async def test_every_key_the_throttle_leaves_behind_is_bounded(
        self, throttling_enabled: None
    ) -> None:
        """Nothing may persist for ever, whatever path produced it."""
        throttle = StepUpThrottle()
        client = await redis_client()
        shared = fresh_ip()

        for _ in range(settings.security.step_up_max_attempts + 2):
            user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
            try:
                await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=shared)
            except RateLimitExceededError:
                pass

        unbounded: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await client.scan(cursor=cursor, match="stepup:*", count=500)
            for raw in batch:
                key = raw if isinstance(raw, str) else raw.decode()
                if int(await client.ttl(key)) < 0:
                    unbounded.append(key)
            if cursor == 0:
                break

        assert not unbounded, f"{len(unbounded)} step-up key(s) with no expiry: {unbounded[:3]}"

    async def test_nothing_raw_reaches_the_keys(self, throttling_enabled: None) -> None:
        throttle = StepUpThrottle()
        user_id, tenant_id, ip = uuid.uuid4(), uuid.uuid4(), fresh_ip()

        keys = [
            throttle.user_key(user_id=user_id, tenant_id=tenant_id),
            throttle.address_key(ip),
        ]
        joined = " ".join(keys)

        assert ip not in joined
        assert str(user_id) not in joined
        assert str(tenant_id) not in joined
        assert "@" not in joined


# ---------------------------------------------------------------------------
# Forbidden-command audit, executed rather than asserted in prose
# ---------------------------------------------------------------------------


class TestNoForbiddenRedisCommandOnTheAuthOrErasurePath:
    """What the code sends, read off the wire.

    Every previous statement that this codebase avoids `GETDEL` and friends was
    a claim about the source. Source review misses two things: a command reached
    through a library helper, and a command issued *inside* a Lua script, where
    no amount of reading the Python tells you what Redis actually ran. `MONITOR`
    reports both.

    The list is not arbitrary. `GETDEL` and `UNLINK` do not exist on the
    deployed Redis 3.0.504 and would fail at runtime — that gap has already
    produced one production defect. `KEYS` walks the whole keyspace and blocks
    the single-threaded server, which on a production instance is an outage.
    `FLUSHDB`, `FLUSHALL` and `SWAPDB` destroy other tenants' data.
    """

    FORBIDDEN: ClassVar[set[str]] = {"GETDEL", "UNLINK", "KEYS", "FLUSHDB", "FLUSHALL", "SWAPDB"}

    async def test_the_step_up_and_erasure_paths_send_nothing_forbidden(
        self, throttling_enabled: None
    ) -> None:
        import threading

        import redis as sync_redis

        redis_settings = settings.redis
        observed: list[str] = []
        listening = threading.Event()

        def watch() -> None:
            client = sync_redis.Redis(
                host=redis_settings.host, port=redis_settings.port, protocol=2
            )
            with client.monitor() as monitor:
                listening.set()
                for command in monitor.listen():
                    observed.append(str(command.get("command", "")))

        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        assert listening.wait(timeout=10), "MONITOR did not start"
        await asyncio.sleep(0.5)

        # --- the step-up path, including the script and the cleanup ---------
        throttle = StepUpThrottle()
        user_id, tenant_id, ip = uuid.uuid4(), uuid.uuid4(), fresh_ip()
        for _ in range(settings.security.step_up_max_attempts + 1):
            try:
                await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=ip)
            except RateLimitExceededError:
                pass
        await throttle.clear_user_attempts(user_id=user_id, tenant_id=tenant_id)

        # --- the erasure path's cache invalidation --------------------------
        from app.core.redis import CacheClient

        cache = CacheClient()
        await cache.set("probe", "value", tenant_id=str(tenant_id), ttl_seconds=60)
        await cache.invalidate_tenant(str(tenant_id))

        await asyncio.sleep(0.7)

        verbs = {line.split()[0].strip('"').upper() for line in observed if line.strip()}
        assert verbs, "MONITOR captured nothing; the audit would pass vacuously"

        used = sorted(verbs & self.FORBIDDEN)
        assert not used, f"forbidden command(s) on the wire: {used}"

        # The audit is only meaningful if it saw the work it was auditing.
        assert "EVAL" in verbs, f"the step-up script never ran; captured {sorted(verbs)}"
        assert "SCAN" in verbs, "erasure did not use SCAN, which is how KEYS is avoided"
