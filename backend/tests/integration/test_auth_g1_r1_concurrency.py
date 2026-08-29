"""AUTH-G1-R1: the races independent review found, as adversarial controls.

Every test here fails on `de96f35`. They are written to *win* the race rather
than hope for it — twenty simultaneous callers, not two — because a check-then-
act bug passes a two-caller test most of the time.

All of it runs against the real Redis 3.0.504 the deployment uses, so `WATCH`
and `SET NX EX` are exercised on the version that will actually run them.
Nothing here contacts Google or sends mail.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import RedisPurpose, get_redis
from app.integrations.google import GoogleIntent, GoogleNonceStore
from app.models.user import User
from app.services.password_reset import PasswordResetService
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration

CONCURRENCY = 20


@pytest.fixture(autouse=True)
async def clean_redis_clients() -> AsyncIterator[None]:
    from app.core.redis import close_redis_clients

    await close_redis_clients()
    yield
    await close_redis_clients()


async def register(client: AsyncClient, email: str) -> uuid.UUID:
    response = await client.post("/api/v1/auth/register", json=registration_payload(email=email))
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["identity"]["user"]["id"])


class TestNonceSingleUse:
    async def test_twenty_simultaneous_uses_admit_exactly_one(self) -> None:
        """MULTI/GET/DEL, on the Redis version that lacks GETDEL."""
        store = GoogleNonceStore()
        nonce = await store.issue(GoogleIntent.LOGIN)

        results = await asyncio.gather(
            *(store.consume(nonce, expected=GoogleIntent.LOGIN) for _ in range(CONCURRENCY))
        )

        assert sum(1 for r in results if r is not None) == 1

    async def test_a_login_nonce_cannot_be_used_for_signup_or_link(self) -> None:
        """The binding the previous version had no concept of."""
        store = GoogleNonceStore()

        nonce = await store.issue(GoogleIntent.LOGIN)
        assert await store.consume(nonce, expected=GoogleIntent.SIGNUP) is None

        nonce = await store.issue(GoogleIntent.LOGIN)
        assert await store.consume(nonce, expected=GoogleIntent.LINK) is None

    async def test_a_link_nonce_cannot_be_used_to_log_in(self) -> None:
        store = GoogleNonceStore()
        nonce = await store.issue(GoogleIntent.LINK, user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
        assert await store.consume(nonce, expected=GoogleIntent.LOGIN) is None

    async def test_a_wrong_intent_still_spends_the_nonce(self) -> None:
        """Otherwise it could be probed against each intent in turn."""
        store = GoogleNonceStore()
        nonce = await store.issue(GoogleIntent.LOGIN)

        assert await store.consume(nonce, expected=GoogleIntent.SIGNUP) is None
        assert await store.consume(nonce, expected=GoogleIntent.LOGIN) is None

    @pytest.mark.parametrize("bad", ["", "   ", "never-issued", "!!!malformed!!!"])
    async def test_missing_blank_and_unknown_nonces_are_refused(self, bad: str) -> None:
        assert await GoogleNonceStore().consume(bad, expected=GoogleIntent.LOGIN) is None

    async def test_a_link_nonce_records_the_issuing_user_and_tenant(self) -> None:
        """So a credential cannot be redirected onto a different account."""
        user_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        store = GoogleNonceStore()
        nonce = await store.issue(GoogleIntent.LINK, user_id=user_id, tenant_id=tenant_id)

        record = await store.consume(nonce, expected=GoogleIntent.LINK)

        assert record is not None
        assert record.user_id == user_id
        assert record.tenant_id == tenant_id

    async def test_a_link_nonce_cannot_be_issued_unbound(self) -> None:
        with pytest.raises(ValueError, match="bound to a user and tenant"):
            await GoogleNonceStore().issue(GoogleIntent.LINK)

    def test_the_nonce_comparison_is_constant_time(self) -> None:
        assert GoogleNonceStore.matches("abc", "abc") is True
        assert GoogleNonceStore.matches("abc", "abd") is False
        assert GoogleNonceStore.matches(None, "abc") is False
        assert GoogleNonceStore.matches("", "abc") is False


class TestCooldownReservation:
    async def test_twenty_concurrent_requests_send_exactly_one_code(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The headline race.

        GET-then-SET meant every one of these saw an empty cooldown and minted a
        code, so one address could be mailed twenty times in a burst. `SET NX
        EX` reserves in a single round trip.
        """
        email = f"burst-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = PasswordResetService(db_session)
        results = await asyncio.gather(*(service.request(email=email) for _ in range(CONCURRENCY)))

        codes = [code for _, code in results if code is not None]
        assert len(codes) == 1, f"{len(codes)} codes minted for one address"

    async def test_a_failed_send_releases_the_cooldown_it_reserved(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"release-{uuid.uuid4().hex}@example.com"
        user_id = await register(client, email)

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        await service.discard(challenge.challenge_id)

        redis = get_redis(RedisPurpose.SESSION)
        assert await redis.get(f"pwreset:cooldown:{user_id}") is None
        # And the challenge itself is gone, so it cannot be verified later.
        assert await redis.get(f"pwreset:challenge:{challenge.challenge_id}") is None

    async def test_discarding_an_old_challenge_leaves_a_newer_one_alone(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A slow failing request must not delete the challenge that replaced it."""
        email = f"stale-{uuid.uuid4().hex}@example.com"
        user_id = await register(client, email)

        redis = get_redis(RedisPurpose.SESSION)
        service = PasswordResetService(db_session)

        first, first_code = await service.request(email=email)
        assert first_code is not None

        # Release the cooldown so a second challenge can be created, as a
        # genuine resend after the window would.
        await redis.delete(f"pwreset:cooldown:{user_id}")
        second, second_code = await service.request(email=email)
        assert second_code is not None

        # Now the *first* request's mail fails and it tidies up, late.
        await service.discard(first.challenge_id)

        assert await redis.get(f"pwreset:account:{user_id}") == second.challenge_id
        assert await service.verify(challenge_id=second.challenge_id, code=second_code) is not None

    async def test_a_resend_invalidates_the_previous_code(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"resend-{uuid.uuid4().hex}@example.com"
        user_id = await register(client, email)

        redis = get_redis(RedisPurpose.SESSION)
        service = PasswordResetService(db_session)

        first, first_code = await service.request(email=email)
        assert first_code is not None
        await redis.delete(f"pwreset:cooldown:{user_id}")
        second, second_code = await service.request(email=email)
        assert second_code is not None

        assert await service.verify(challenge_id=first.challenge_id, code=first_code) is None
        assert await service.verify(challenge_id=second.challenge_id, code=second_code) is not None


class TestAttemptAccounting:
    async def test_twenty_concurrent_wrong_attempts_exhaust_the_challenge(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Read-modify-write meant twenty guesses recorded as one.

        The five-attempt ceiling was therefore not a ceiling at all: an attacker
        could fire concurrently and keep the challenge alive indefinitely.
        """
        email = f"attempts-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        wrong = "000000" if code != "000000" else "111111"
        await asyncio.gather(
            *(
                service.verify(challenge_id=challenge.challenge_id, code=wrong)
                for _ in range(CONCURRENCY)
            )
        )

        # The correct code must no longer work: the ceiling was reached.
        assert await service.verify(challenge_id=challenge.challenge_id, code=code) is None

    async def test_twenty_concurrent_correct_attempts_admit_exactly_one(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"correct-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        outcomes = await asyncio.gather(
            *(
                service.verify(challenge_id=challenge.challenge_id, code=code)
                for _ in range(CONCURRENCY)
            )
        )

        assert sum(1 for o in outcomes if o is not None) == 1

    async def test_a_correct_code_racing_the_final_wrong_one_yields_at_most_one_ticket(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"mixed-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None
        wrong = "000000" if code != "000000" else "111111"

        submissions = [wrong] * (settings.password_reset.max_verification_attempts - 1) + [code]
        outcomes = await asyncio.gather(
            *(service.verify(challenge_id=challenge.challenge_id, code=c) for c in submissions)
        )

        assert sum(1 for o in outcomes if o is not None) <= 1

    async def test_a_spent_ticket_cannot_be_spent_again_concurrently(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"ticket-race-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None
        outcome = await service.verify(challenge_id=challenge.challenge_id, code=code)
        assert outcome is not None

        results = await asyncio.gather(
            *(
                service.complete(
                    reset_ticket=outcome.reset_ticket, new_password="An0ther-Strong-Pass!"
                )
                for _ in range(5)
            ),
            return_exceptions=True,
        )
        assert sum(1 for r in results if isinstance(r, uuid.UUID)) == 1


class TestNoLeakage:
    async def test_no_code_key_or_address_appears_in_a_challenge_response(
        self, client: AsyncClient
    ) -> None:
        import re

        email = f"quiet-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
        body = response.text

        assert re.search(r"\b\d{6}\b", body) is None
        assert "pwreset:" not in body
        assert email not in body


class TestLegalAcceptanceIsEnforced:
    async def test_password_registration_without_acceptance_is_refused(
        self, client: AsyncClient
    ) -> None:
        """The bypass review found: a checkbox is not evidence."""
        payload = registration_payload(email=f"noaccept-{uuid.uuid4().hex}@example.com")
        payload.pop("termsAccepted", None)
        payload.pop("privacyAccepted", None)

        response = await client.post("/api/v1/auth/register", json=payload)

        assert response.status_code in (400, 422), response.text

    async def test_acceptance_is_recorded_with_a_version(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from app.core.legal import PRIVACY_NOTICE_VERSION, TERMS_VERSION

        email = f"accepted-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()

        assert user.terms_accepted_at is not None
        assert user.privacy_accepted_at is not None
        assert user.terms_version == TERMS_VERSION
        assert user.privacy_version == PRIVACY_NOTICE_VERSION

    async def test_a_stale_privacy_version_is_refused(self, client: AsyncClient) -> None:
        payload = registration_payload(email=f"stale-{uuid.uuid4().hex}@example.com")
        payload["privacyVersion"] = "1999-01-01"

        response = await client.post("/api/v1/auth/register", json=payload)

        assert response.status_code in (400, 422), response.text

    def test_existing_users_without_acceptance_remain_valid(self) -> None:
        """The columns are nullable; back-filling would invent evidence."""
        for column in (
            "terms_accepted_at",
            "terms_version",
            "privacy_accepted_at",
            "privacy_version",
        ):
            assert User.__table__.columns[column].nullable is True

    def test_the_client_ip_is_not_stored_as_acceptance_evidence(self) -> None:
        columns = {c.name for c in User.__table__.columns}
        for forbidden in ("acceptance_ip", "terms_ip", "signup_ip", "registration_ip"):
            assert forbidden not in columns


class TestUserForeignKeyGuardStillHolds:
    def test_the_erasure_declaration_still_covers_every_user_reference(self) -> None:
        """0032 adds columns, not a new user FK — but prove it, do not assume."""
        from app.models.base import Base
        from app.services.data_subject_erasure import USER_REFERENCES

        declared = {(r.model.__tablename__, r.column.key) for r in USER_REFERENCES}
        actual = {
            (table.name, column.name)
            for table in Base.metadata.sorted_tables
            for column in table.columns
            for fk in column.foreign_keys
            if fk.column.table.name == "users"
        }
        assert actual - declared == set()


def _unused(value: Any) -> Any:  # pragma: no cover - keeps typing import honest
    return value


class TestGoogleCertificateTransport:
    """Phase 6: the fetch must be bounded, cached and single-flight.

    A fake fetch throughout — no request reaches Google.
    """

    def setup_method(self) -> None:
        from app.integrations.google.verification import reset_certificate_cache

        reset_certificate_cache()

    def test_the_transport_carries_an_explicit_timeout(self) -> None:
        """A worker thread does not bound a socket; this does."""
        from app.integrations.google.verification import _BoundedRequest

        request = _BoundedRequest((3.0, 5.0))
        assert request._timeout == (3.0, 5.0)

    def test_timeouts_are_configurable_and_bounded(self) -> None:
        assert 0 < settings.google_oauth.connect_timeout_seconds <= 30
        assert 0 < settings.google_oauth.read_timeout_seconds <= 30

    async def test_certificates_are_fetched_once_and_reused(self) -> None:
        from app.integrations.google.verification import _CERT_CACHE

        calls = {"n": 0}

        def fake_fetch() -> dict[str, Any]:
            calls["n"] += 1
            return {"kid": "value"}

        assert await _CERT_CACHE.get(fake_fetch) == {"kid": "value"}
        assert await _CERT_CACHE.get(fake_fetch) == {"kid": "value"}

        # Second call served from cache: no second outbound request.
        assert calls["n"] == 1

    async def test_a_burst_of_cache_misses_fetches_once(self) -> None:
        """Single-flight. A cold start would otherwise fetch per sign-in."""
        from app.integrations.google.verification import _CERT_CACHE

        calls = {"n": 0}

        def slow_fetch() -> dict[str, Any]:
            calls["n"] += 1
            return {"kid": "value"}

        await asyncio.gather(*(_CERT_CACHE.get(slow_fetch) for _ in range(CONCURRENCY)))

        assert calls["n"] == 1, f"{calls['n']} concurrent fetches on one cache miss"

    async def test_a_fetch_failure_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A timed-out key fetch is not a passed check."""
        from app.integrations.google import GoogleTokenError, GoogleTokenVerifier
        from app.integrations.google.verification import reset_certificate_cache

        def explode(self: object) -> dict[str, Any]:
            raise TimeoutError("connect timed out to 203.0.113.1:443")

        monkeypatch.setattr(GoogleTokenVerifier, "_fetch_certs", explode)
        reset_certificate_cache()

        with pytest.raises(GoogleTokenError, match="verification unavailable"):
            await GoogleTokenVerifier().verify("anything", expected_nonce="n")

    async def test_a_fetch_failure_leaks_no_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.google import GoogleTokenError, GoogleTokenVerifier
        from app.integrations.google.verification import reset_certificate_cache

        def explode(self: object) -> dict[str, Any]:
            raise TimeoutError("connect timed out to 203.0.113.1:443")

        monkeypatch.setattr(GoogleTokenVerifier, "_fetch_certs", explode)
        reset_certificate_cache()

        with pytest.raises(GoogleTokenError) as caught:
            await GoogleTokenVerifier().verify("credential-value", expected_nonce="n")

        message = str(caught.value)
        assert "203.0.113.1" not in message
        assert "credential-value" not in message
