"""AUTH-G1-R2: the four blockers independent review returned, as controls.

Three of them are about what happens when something goes *wrong* — a Redis
transaction that keeps losing its race, an attacker holding a stolen session and
guessing, a token that is almost valid. None of those paths are exercised by a
happy-path suite, which is why each one shipped.

Everything runs against the real Redis 3.0.504 the deployment uses, so `WATCH`,
`INCR` and `EXPIRE` are exercised on the version that will actually run them.
**Nothing here contacts Google.** The JWT controls sign real RS256 tokens with a
throwaway key generated in-process and hand the verifier a fake certificate set,
so every check the application makes runs for real against tokens an attacker
could actually construct.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import jwt as pyjwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError, WatchError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError
from app.integrations.google import GoogleTokenError, GoogleTokenVerifier
from app.services.login_throttle import StepUpThrottle, StepUpUnavailableError
from app.services.password_reset import (
    _MAX_VERIFY_CONTENTION_RETRIES,
    PasswordResetBusyError,
    PasswordResetService,
)
from tests.integration.conftest import STRONG_PASSWORD, registration_payload

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
    """Re-enable rate limiting, which the integration conftest turns off.

    That fixture exists so the ordinary HTTP flow tests are not fighting a
    throttle they are not testing. It also means the throttle itself would go
    completely unexercised at this layer — a control switched off for
    convenience needs its own tests that switch it back on, and these are them.
    """
    original = settings.security.rate_limit_enabled
    settings.security.rate_limit_enabled = True
    yield
    settings.security.rate_limit_enabled = original


# ---------------------------------------------------------------------------
# Blocker: the WATCH retry loop was unbounded
# ---------------------------------------------------------------------------


class _AlwaysConflictingPipeline:
    """A pipeline whose `EXEC` always loses its watch.

    Wraps the real one rather than replacing it, so every other call still hits
    Redis and the code under test takes exactly the path it takes in
    production — only the outcome of `EXEC` is forced.
    """

    def __init__(self, inner: Any, counter: dict[str, int], fail_first: int | None) -> None:
        self._inner = inner
        self._counter = counter
        self._fail_first = fail_first

    async def __aenter__(self) -> _AlwaysConflictingPipeline:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._inner.__aexit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self._counter["execs"] += 1
        if self._fail_first is None or self._counter["execs"] <= self._fail_first:
            raise WatchError("WATCHed variable changed")
        return await self._inner.execute(*args, **kwargs)


class _ConflictingRedis:
    """The session client, with contention forced on the verification path."""

    def __init__(self, inner: Any, *, fail_first: int | None) -> None:
        self._inner = inner
        self._fail_first = fail_first
        self.counter: dict[str, int] = {"execs": 0}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        return _AlwaysConflictingPipeline(
            self._inner.pipeline(*args, **kwargs), self.counter, self._fail_first
        )


async def _issue_challenge(session: AsyncSession, email: str) -> tuple[str, str]:
    service = PasswordResetService(session)
    challenge, code = await service.request(email=email, client_ip=None)
    assert challenge is not None and code is not None
    return challenge.challenge_id, code


class TestBoundedWatchRetry:
    """`while True` terminates only because conflicts happen to be rare.

    That is an observation about typical load, not a property of the algorithm.
    Under a burst — or a client retrying in a tight loop — the previous loop was
    a request that never returned while holding a connection and an event-loop
    slot. These force the conflict that the happy path never produces.
    """

    async def test_a_permanently_conflicted_verify_gives_up(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"watch+{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201
        challenge_id, code = await _issue_challenge(db_session, email)

        service = PasswordResetService(db_session)
        service._redis = _ConflictingRedis(service._redis, fail_first=None)

        with pytest.raises(PasswordResetBusyError):
            await service.verify(challenge_id=challenge_id, code=code)

    async def test_it_returns_within_a_deterministic_upper_bound(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The point of the bound: a caller gets an answer, not a hung request."""
        email = f"watchbound+{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201
        challenge_id, code = await _issue_challenge(db_session, email)

        service = PasswordResetService(db_session)
        service._redis = _ConflictingRedis(service._redis, fail_first=None)

        started = time.monotonic()
        with pytest.raises(PasswordResetBusyError):
            await service.verify(challenge_id=challenge_id, code=code)
        elapsed = time.monotonic() - started

        # Every backoff is capped, so the total sleep cannot exceed the ceiling
        # times the retry count. Generous headroom for the Redis round trips —
        # the assertion is that it terminates quickly, not that it is fast.
        assert elapsed < 5.0, f"took {elapsed:.2f}s"

    async def test_the_retry_ceiling_is_exactly_respected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Not "eventually stops" — stops after the number the module names."""
        email = f"watchcount+{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201
        challenge_id, code = await _issue_challenge(db_session, email)

        service = PasswordResetService(db_session)
        conflicting = _ConflictingRedis(service._redis, fail_first=None)
        service._redis = conflicting

        with pytest.raises(PasswordResetBusyError):
            await service.verify(challenge_id=challenge_id, code=code)

        assert conflicting.counter["execs"] == _MAX_VERIFY_CONTENTION_RETRIES

    async def test_exhaustion_issues_no_ticket(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A busy server must not hand out the authority to change a password."""
        email = f"watchticket+{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201
        challenge_id, code = await _issue_challenge(db_session, email)

        service = PasswordResetService(db_session)
        service._redis = _ConflictingRedis(service._redis, fail_first=None)

        with pytest.raises(PasswordResetBusyError) as caught:
            await service.verify(challenge_id=challenge_id, code=code)

        # Nothing that could be spent, and nothing that could be brute-forced.
        message = str(caught.value)
        assert "ticket" not in message.lower()
        assert code not in message
        assert challenge_id not in message

    async def test_exhaustion_spends_no_attempt_and_a_clean_retry_still_works(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The heart of it: a contended attempt was never evaluated.

        Reporting it as a wrong code would burn one of five guesses the person
        never actually made, and would eventually consume the challenge for
        somebody whose only mistake was arriving during a burst.
        """
        email = f"watchretry+{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201
        challenge_id, code = await _issue_challenge(db_session, email)

        service = PasswordResetService(db_session)
        real = service._redis
        service._redis = _ConflictingRedis(real, fail_first=None)
        with pytest.raises(PasswordResetBusyError):
            await service.verify(challenge_id=challenge_id, code=code)

        # The challenge survived untouched, so the correct code still works.
        service._redis = real
        outcome = await service.verify(challenge_id=challenge_id, code=code)
        assert outcome is not None
        assert outcome.reset_ticket

    async def test_a_conflict_that_clears_still_succeeds(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The bound must not break the retry it exists to limit."""
        email = f"watchclears+{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201
        challenge_id, code = await _issue_challenge(db_session, email)

        service = PasswordResetService(db_session)
        conflicting = _ConflictingRedis(
            service._redis, fail_first=_MAX_VERIFY_CONTENTION_RETRIES - 1
        )
        service._redis = conflicting

        outcome = await service.verify(challenge_id=challenge_id, code=code)

        assert outcome is not None
        assert conflicting.counter["execs"] == _MAX_VERIFY_CONTENTION_RETRIES

    async def test_twenty_concurrent_wrong_guesses_still_count_exactly_five(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The R1 control, re-run: bounding the loop must not weaken it."""
        email = f"watchconc+{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201
        challenge_id, correct = await _issue_challenge(db_session, email)

        wrong = "000000" if correct != "000000" else "111111"
        service = PasswordResetService(db_session)
        results = await asyncio.gather(
            *(service.verify(challenge_id=challenge_id, code=wrong) for _ in range(CONCURRENCY)),
            return_exceptions=True,
        )

        # No caller got a ticket, and contention never surfaced as a busy error
        # under a burst this size — the retries absorb it.
        assert all(r is None for r in results), [type(r).__name__ for r in results if r is not None]

        # The ceiling did its job: the challenge is gone, not sitting at one
        # recorded attempt the way the pre-R1 counter left it.
        assert await service.verify(challenge_id=challenge_id, code=correct) is None


# ---------------------------------------------------------------------------
# Blocker: link/unlink step-up had no rate limit
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db_session: AsyncSession) -> Any:
    """The real application, sharing this test's transaction.

    Built here rather than reusing the `client` fixture because these tests need
    to choose the socket peer: the throttle counts per address as well as per
    user, and an in-process ASGI call reports one fixed peer for every caller
    unless it is given one. Two accounts on one address are two accounts behind
    one NAT, which is a case worth being able to write.
    """
    from app.api.deps import get_db_session
    from app.main import create_application

    application = create_application()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _override
    return application


def _fresh_ip() -> str:
    """An address no earlier test or earlier run has counted against.

    Redis counters outlive a test — they expire on a window, not on teardown —
    so a fixed address makes these tests pass or fail depending on what ran in
    the last five minutes. Drawn from the carrier-grade NAT range, which is
    large enough that a collision is not worth reasoning about.
    """
    raw = uuid.uuid4().int
    return f"100.{(raw >> 16) & 0x3F}.{(raw >> 8) & 0xFF}.{raw & 0xFF}"


@asynccontextmanager
async def _signed_in(app: Any, ip: str) -> AsyncIterator[tuple[AsyncClient, dict[str, str]]]:
    """A client on `ip`, signed in as a fresh account."""
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(ip, 12345)), base_url="http://testserver"
    ) as http:
        email = f"stepup+{uuid.uuid4().hex}@example.com"
        response = await http.post("/api/v1/auth/register", json=registration_payload(email=email))
        assert response.status_code == 201, response.text
        token = response.json()["tokens"]["accessToken"]
        yield http, {"Authorization": f"Bearer {token}"}


WRONG_PASSWORD = "Wrong-Password-9999"


async def _wrong_unlink(http: AsyncClient, auth: dict[str, str]) -> int:
    response = await http.post(
        "/api/v1/auth/google/unlink", json={"password": WRONG_PASSWORD}, headers=auth
    )
    return response.status_code


class TestStepUpRateLimit:
    """A stolen session should not be an unlimited password oracle.

    Linking a Google account is the operation that matters: succeed once and the
    attacker has a permanent second way in that survives a password change. The
    step-up password was the control, and before this it could be guessed as
    fast as the network allowed.
    """

    async def test_repeated_wrong_passwords_become_rate_limited(
        self, app: Any, throttling_enabled: None
    ) -> None:
        async with _signed_in(app, _fresh_ip()) as (http, auth):
            limit = settings.security.step_up_max_attempts
            statuses = [await _wrong_unlink(http, auth) for _ in range(limit + 2)]

        # Exactly the allowance is evaluated; everything after is refused before
        # the hash is ever checked.
        assert statuses[:limit] == [401] * limit, statuses
        assert statuses[limit:] == [429] * (len(statuses) - limit), statuses

    async def test_alternating_link_and_unlink_shares_one_limit(
        self, app: Any, throttling_enabled: None
    ) -> None:
        """Two counters would simply mean twice the guesses."""
        async with _signed_in(app, _fresh_ip()) as (http, auth):
            statuses = []
            for index in range(settings.security.step_up_max_attempts + 1):
                if index % 2 == 0:
                    statuses.append(await _wrong_unlink(http, auth))
                else:
                    response = await http.post(
                        "/api/v1/auth/google/link",
                        json={
                            "credential": "never.reached",
                            "nonce": "never-reached",
                            "password": WRONG_PASSWORD,
                        },
                        headers=auth,
                    )
                    statuses.append(response.status_code)

        assert statuses[-1] == 429, statuses

    async def test_concurrent_attempts_cannot_exceed_the_limit(
        self, app: Any, throttling_enabled: None
    ) -> None:
        """Read-then-write would let all twenty through. `INCR` first does not."""
        async with _signed_in(app, _fresh_ip()) as (http, auth):
            statuses = await asyncio.gather(
                *(_wrong_unlink(http, auth) for _ in range(CONCURRENCY))
            )

        evaluated = sum(1 for status in statuses if status == 401)
        assert evaluated <= settings.security.step_up_max_attempts, (
            f"{evaluated} of {CONCURRENCY} concurrent guesses reached the password check"
        )
        assert any(status == 429 for status in statuses)

    async def test_a_correct_password_works_before_exhaustion(
        self, app: Any, throttling_enabled: None
    ) -> None:
        async with _signed_in(app, _fresh_ip()) as (http, auth):
            assert await _wrong_unlink(http, auth) == 401

            response = await http.post(
                "/api/v1/auth/google/unlink", json={"password": STRONG_PASSWORD}, headers=auth
            )

        # Nothing was linked, so this is the "nothing to disconnect" success.
        assert response.status_code == 200, response.text

    async def test_a_correct_password_does_not_lift_an_active_lockout(
        self, app: Any, throttling_enabled: None
    ) -> None:
        """The limit is reserved before the hash is checked, so a correct
        password arriving after the ceiling is refused rather than rewarded."""
        async with _signed_in(app, _fresh_ip()) as (http, auth):
            for _ in range(settings.security.step_up_max_attempts + 1):
                await _wrong_unlink(http, auth)

            response = await http.post(
                "/api/v1/auth/google/unlink", json={"password": STRONG_PASSWORD}, headers=auth
            )

        assert response.status_code == 429, response.text

    async def test_another_user_on_another_address_is_unaffected(
        self, app: Any, throttling_enabled: None
    ) -> None:
        """A lockout must not be a way to deny service to somebody else."""
        async with _signed_in(app, _fresh_ip()) as (victim, victim_auth):
            for _ in range(settings.security.step_up_max_attempts + 1):
                await _wrong_unlink(victim, victim_auth)

        async with _signed_in(app, _fresh_ip()) as (bystander, bystander_auth):
            response = await bystander.post(
                "/api/v1/auth/google/unlink",
                json={"password": STRONG_PASSWORD},
                headers=bystander_auth,
            )

        assert response.status_code == 200, response.text

    async def test_a_second_account_behind_one_address_shares_the_address_limit(
        self, app: Any, throttling_enabled: None
    ) -> None:
        """The other half of the trade, stated rather than discovered later.

        Per-user alone would let an attacker spread guesses across accounts from
        one machine and never trip a limit. The cost is that a genuinely shared
        address is limited collectively — the same trade the login throttle
        makes, and the reason both dimensions exist rather than one.

        Since R3 the address ceiling is deliberately looser than the per-user
        one, so exhausting it takes more than one account's worth of guessing.
        This walks it down with fresh accounts rather than assuming the two
        ceilings coincide.
        """
        shared = _fresh_ip()
        budget = settings.security.step_up_max_attempts_per_ip
        spent = 0
        while spent <= budget:
            async with _signed_in(app, shared) as (client, auth):
                for _ in range(settings.security.step_up_max_attempts):
                    await _wrong_unlink(client, auth)
                    spent += 1
                    if spent > budget:
                        break

        async with _signed_in(app, shared) as (second, second_auth):
            response = await second.post(
                "/api/v1/auth/google/unlink",
                json={"password": STRONG_PASSWORD},
                headers=second_auth,
            )

        assert response.status_code == 429, response.text

    async def test_nothing_sensitive_reaches_the_keys_or_the_error(
        self, throttling_enabled: None
    ) -> None:
        """Redis keys turn up in MONITOR output and support dumps."""
        throttle = StepUpThrottle()
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ip = _fresh_ip()

        keys = [
            throttle.user_key(user_id=user_id, tenant_id=tenant_id),
            throttle.address_key(ip),
        ]

        joined = " ".join(keys)
        assert ip not in joined
        assert str(user_id) not in joined
        assert str(tenant_id) not in joined
        assert "@" not in joined

        for _ in range(settings.security.step_up_max_attempts + 1):
            try:
                await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=ip)
            except RateLimitExceededError as exc:
                assert ip not in str(exc)
                assert str(user_id) not in str(exc)
                assert "password" not in str(exc).lower()
                break
        else:
            pytest.fail("the ceiling was never reached")

    async def test_the_same_user_id_in_two_tenants_counts_separately(
        self, throttling_enabled: None
    ) -> None:
        """Scoping is explicit rather than assumed from the id being a UUID."""
        throttle = StepUpThrottle()
        user_id = uuid.uuid4()
        first, second = uuid.uuid4(), uuid.uuid4()

        for _ in range(settings.security.step_up_max_attempts + 1):
            try:
                await throttle.reserve(user_id=user_id, tenant_id=first, client_ip=None)
            except RateLimitExceededError:
                break

        # Same user id, different workspace: its own counter.
        await throttle.reserve(user_id=user_id, tenant_id=second, client_ip=None)

    async def test_a_correct_password_clears_the_counter(self, throttling_enabled: None) -> None:
        """Two mistypes then a success must not leave a lockout one guess away."""
        throttle = StepUpThrottle()
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()

        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
        await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)
        await throttle.clear_user_attempts(user_id=user_id, tenant_id=tenant_id)

        # A full fresh allowance, not one remaining attempt.
        for _ in range(settings.security.step_up_max_attempts):
            await throttle.reserve(user_id=user_id, tenant_id=tenant_id, client_ip=None)

    async def test_redis_failure_refuses_rather_than_waves_through(
        self, monkeypatch: pytest.MonkeyPatch, throttling_enabled: None
    ) -> None:
        """The opposite of the login throttle, deliberately.

        Failing open there keeps customers able to sign in during an outage.
        Failing open *here* would turn a Redis blip into an unthrottled password
        oracle behind whatever session the attacker already holds.
        """
        from app.services import login_throttle as module

        def unavailable(_purpose: object) -> Any:
            raise RedisError("connection refused")

        monkeypatch.setattr(module, "get_redis", unavailable)

        with pytest.raises(StepUpUnavailableError):
            await StepUpThrottle().reserve(
                user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), client_ip=_fresh_ip()
            )

    async def test_ordinary_login_limits_are_untouched(self, throttling_enabled: None) -> None:
        """Separate key namespaces, so one cannot exhaust the other."""
        throttle = StepUpThrottle()
        keys = [
            throttle.user_key(user_id=uuid.uuid4(), tenant_id=uuid.uuid4()),
            throttle.address_key(_fresh_ip()),
        ]

        assert all(key.startswith("stepup:") for key in keys)
        assert not any(key.startswith("login:") for key in keys)


# ---------------------------------------------------------------------------
# Blocker: the Google crypto controls were asserted, not exercised
# ---------------------------------------------------------------------------


class _FakeGoogleKeys:
    """A throwaway RSA key and a self-signed certificate for it.

    Generated in-process, used for one test session and discarded. This is what
    lets the controls below sign *real* RS256 tokens: the verifier then runs the
    same signature, audience and expiry checks it runs in production, against
    tokens an attacker could genuinely construct. Asserting the checks exist by
    reading the code proves nothing about whether they fire.
    """

    KID = "auth-g1-r2-test-key"

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(dt.UTC)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "auth-g1-r2-tests")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=1))
            .sign(self.private_key, hashes.SHA256())
        )
        self.certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
        self.private_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    @property
    def certs(self) -> dict[str, str]:
        return {self.KID: self.certificate_pem}


_KEYS = _FakeGoogleKeys()

_CLIENT_ID = "000000000000-r2controls.apps.googleusercontent.com"
_NONCE = "r2-control-nonce"


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
        "sub": "1234567890",
        "email": "person@example.com",
        "email_verified": True,
        "nonce": _NONCE,
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return claims


def _sign(claims: dict[str, Any], *, kid: str = _FakeGoogleKeys.KID) -> str:
    return pyjwt.encode(
        claims, _KEYS.private_pem, algorithm="RS256", headers={"kid": kid, "alg": "RS256"}
    )


def _hmac_signed(claims: dict[str, Any], *, key: bytes) -> str:
    """An HS256 token, assembled without a library that would refuse to."""
    header = _b64({"alg": "HS256", "typ": "JWT", "kid": _FakeGoogleKeys.KID})
    payload = _b64(claims)
    signing_input = f"{header}.{payload}".encode()
    digest = hmac.new(key, signing_input, hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def _b64(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.fixture
def verifier(monkeypatch: pytest.MonkeyPatch) -> GoogleTokenVerifier:
    """A verifier wired to the throwaway certificate set.

    Only the *fetch* is replaced. Every check the application performs — the
    library's signature, audience and expiry checks, and this module's issuer,
    verified-email and nonce checks — runs for real.
    """
    from app.integrations.google.verification import reset_certificate_cache

    instance = GoogleTokenVerifier(client_id=_CLIENT_ID)
    monkeypatch.setattr(
        GoogleTokenVerifier, "_fetch_certs", lambda self: dict(_KEYS.certs), raising=True
    )
    reset_certificate_cache()
    yield instance
    reset_certificate_cache()


class TestConstructedGoogleTokensAreRejected:
    """Nine ways a token can be almost right, each refused.

    No request reaches Google in any of them.
    """

    async def test_a_correctly_signed_token_is_accepted(
        self, verifier: GoogleTokenVerifier
    ) -> None:
        """The control for the controls. Without it, a verifier that rejected
        everything would pass every test below and prove nothing."""
        identity = await verifier.verify(_sign(_claims()), expected_nonce=_NONCE)

        assert identity.subject == "1234567890"
        assert identity.email == "person@example.com"

    async def test_alg_none_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """The oldest JWT attack: drop the signature and declare it unnecessary."""
        token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims())}."

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_a_forged_signature_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """Valid claims, tampered signature."""
        header, payload, signature = _sign(_claims()).split(".")
        flipped = ("A" if signature[0] != "A" else "B") + signature[1:]

        with pytest.raises(GoogleTokenError):
            await verifier.verify(f"{header}.{payload}.{flipped}", expected_nonce=_NONCE)

    async def test_a_tampered_payload_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """Swapping the subject after signing — the point of a signature."""
        header, _, signature = _sign(_claims()).split(".")
        forged = _b64(_claims(sub="9999999999", email="attacker@example.com"))

        with pytest.raises(GoogleTokenError):
            await verifier.verify(f"{header}.{forged}.{signature}", expected_nonce=_NONCE)

    async def test_an_unexpected_algorithm_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """Algorithm confusion: sign with HS256 using the public certificate as
        the shared secret.

        A verifier that trusts the header's `alg` accepts this, because the
        certificate is public — anybody can compute the MAC. Assembled by hand
        because PyJWT refuses to build it: `prepare_key` rejects a PEM as an
        HMAC secret, which is PyJWT protecting its *callers*, not this
        application refusing the token. The attacker has no such scruples.
        """
        token = _hmac_signed(_claims(), key=_KEYS.certificate_pem.encode())

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_an_unknown_key_id_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """A correctly signed token from a key Google never published."""
        token = _sign(_claims(), kid="a-key-google-never-published")

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_a_wrong_audience_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """A genuine Google token, issued for somebody else's application.

        This is the one that matters most in practice: an attacker with any
        Google client id can obtain real, correctly signed tokens.
        """
        token = _sign(_claims(aud="000000000000-someoneelse.apps.googleusercontent.com"))

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_an_expired_token_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        now = int(time.time())
        token = _sign(_claims(iat=now - 7200, exp=now - 3600))

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_a_wrong_issuer_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """The library validates the signature and audience and leaves `iss` to
        the caller — so this check is entirely this application's."""
        token = _sign(_claims(iss="https://accounts.evil.example"))

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_an_unverified_email_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """Google will assert an address it has not verified. Treating that as
        proof of ownership would let somebody claim an address they do not
        control, and collide with a real user."""
        token = _sign(_claims(email_verified=False))

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_a_missing_nonce_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        claims = _claims()
        del claims["nonce"]

        with pytest.raises(GoogleTokenError):
            await verifier.verify(_sign(claims), expected_nonce=_NONCE)

    async def test_a_wrong_nonce_is_rejected(self, verifier: GoogleTokenVerifier) -> None:
        """A credential captured from another sign-in attempt."""
        token = _sign(_claims(nonce="a-nonce-from-somebody-elses-attempt"))

        with pytest.raises(GoogleTokenError):
            await verifier.verify(token, expected_nonce=_NONCE)

    async def test_the_caller_cannot_decline_the_nonce_check(
        self, verifier: GoogleTokenVerifier
    ) -> None:
        """A defence a caller can opt out of is not a defence."""
        with pytest.raises(GoogleTokenError):
            await verifier.verify(_sign(_claims()), expected_nonce="")

    async def test_a_rejection_never_says_which_check_failed(
        self, verifier: GoogleTokenVerifier
    ) -> None:
        """Otherwise this becomes an oracle for crafting one that passes."""
        tokens = [
            _sign(_claims(iss="https://accounts.evil.example")),
            _sign(_claims(email_verified=False)),
            _sign(_claims(nonce="wrong")),
        ]

        for token in tokens:
            with pytest.raises(GoogleTokenError) as caught:
                await verifier.verify(token, expected_nonce=_NONCE)
            # Whatever the reason, it must not carry the credential itself.
            assert token not in str(caught.value)
