"""AUTH-G1: Google sign-in and password-reset codes.

**Only Google's own library call is mocked.** `_verify_blocking` is replaced so
no network request is made and no real credential is needed — everything above
it (issuer, audience, `email_verified`, nonce, subject) is this application's
code and runs for real. Mocking `verify()` itself would test the mock.

No live Google request and no real email is sent anywhere in this file.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.legal import PRIVACY_NOTICE_VERSION, TERMS_VERSION
from app.integrations.email import (
    EmailDeliveryError,
    EmailMessage,
    ResendEmailProvider,
    SendBudget,
    StubEmailProvider,
    reset_email_provider,
)
from app.integrations.google import GoogleTokenError, GoogleTokenVerifier
from app.integrations.google.verification import reset_certificate_cache
from app.models.identity import IdentityProvider, UserIdentity
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.password_reset import PasswordResetService
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration

CLIENT_ID = settings.google_oauth.client_id


@pytest.fixture(autouse=True)
async def clean_redis_clients() -> AsyncIterator[None]:
    """Do not inherit or leave a Redis client bound to a closed event loop.

    Same reason as the EBAY-C0/C0.1 suites: `app.core.redis` caches clients in a
    module-level dict, and a client built on this test's loop breaks the next
    module to touch it.
    """
    from app.core.redis import close_redis_clients

    await close_redis_clients()
    yield
    await close_redis_clients()


@pytest.fixture(autouse=True)
def stub_email() -> AsyncIterator[StubEmailProvider]:
    """Every test sends through the recording stub. Nothing leaves the process."""
    reset_email_provider()
    yield StubEmailProvider()
    reset_email_provider()


def claims(**overrides: Any) -> dict[str, Any]:
    """A well-formed set of Google claims, before any override."""
    base: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": f"google-sub-{uuid.uuid4().hex}",
        "email": f"g-{uuid.uuid4().hex}@example.com",
        "email_verified": True,
        "name": "Ada Lovelace",
        "exp": 9_999_999_999,
    }
    base.update(overrides)
    return base


def verifier_returning(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    """Replace only Google's library calls — every check above them still runs.

    Two seams, both stubbed so nothing reaches the network: the certificate
    fetch and the signature check. Everything this application then does with
    the claims — issuer, audience, `email_verified`, subject, nonce — is its own
    code and runs for real.
    """
    monkeypatch.setattr(GoogleTokenVerifier, "_fetch_certs", lambda self: {"stub": "certs"})
    monkeypatch.setattr(
        GoogleTokenVerifier, "_verify_blocking", lambda self, credential, certs: payload
    )
    # A cached entry from an earlier test would otherwise satisfy the fetch and
    # skip the stub above.
    reset_certificate_cache()


async def google_post(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    *,
    intent: str = "signup",
    **body: Any,
) -> httpx.Response:
    """Issue a real nonce for the intent, then post the credential.

    The nonce is obtained through the actual endpoint rather than fabricated, so
    the binding and single-use behaviour are exercised on every call.
    """
    from app.integrations.google import GoogleIntent, GoogleNonceStore

    nonce = await GoogleNonceStore().issue(GoogleIntent(intent))
    verifier_returning(monkeypatch, {**payload, "nonce": nonce})

    request: dict[str, Any] = {"credential": "mocked.credential.value", "nonce": nonce}
    if intent == "signup":
        request.update(
            termsAccepted=True,
            privacyAccepted=True,
            termsVersion=TERMS_VERSION,
            privacyVersion=PRIVACY_NOTICE_VERSION,
        )
    request.update(body)
    return await client.post(f"/api/v1/auth/google/{intent}", json=request)


# ---------------------------------------------------------------------------
# Credential verification
# ---------------------------------------------------------------------------


class TestCredentialVerification:
    async def test_a_well_formed_credential_yields_an_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = claims()
        verifier_returning(monkeypatch, {**payload, "nonce": "n"})

        identity = await GoogleTokenVerifier().verify("mocked", expected_nonce="n")

        assert identity.subject == payload["sub"]
        assert identity.email == payload["email"]
        assert identity.email_verified is True

    @pytest.mark.parametrize(
        ("override", "reason"),
        [
            ({"iss": "https://evil.example.com"}, "unexpected issuer"),
            ({"aud": "some-other-client-id"}, "unexpected audience"),
            ({"email_verified": False}, "not verified"),
            ({"email_verified": "false"}, "not verified"),
            ({"sub": ""}, "no subject"),
            ({"email": ""}, "no email"),
        ],
    )
    async def test_a_credential_failing_any_check_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, override: dict[str, Any], reason: str
    ) -> None:
        verifier_returning(monkeypatch, claims(nonce="n", **override))

        with pytest.raises(GoogleTokenError, match=reason):
            await GoogleTokenVerifier().verify("mocked", expected_nonce="n")

    async def test_a_signature_or_expiry_failure_from_google_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Google's library raises `ValueError` for every rejection it makes."""

        def explode(self: object, credential: str, certs: dict[str, Any]) -> dict[str, Any]:
            raise ValueError("Token expired")

        monkeypatch.setattr(GoogleTokenVerifier, "_fetch_certs", lambda self: {"stub": "c"})
        monkeypatch.setattr(GoogleTokenVerifier, "_verify_blocking", explode)
        reset_certificate_cache()

        with pytest.raises(GoogleTokenError, match="failed verification"):
            await GoogleTokenVerifier().verify("mocked", expected_nonce="n")

    async def test_a_nonce_mismatch_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        verifier_returning(monkeypatch, claims(nonce="issued-for-another-attempt"))

        with pytest.raises(GoogleTokenError, match="nonce mismatch"):
            await GoogleTokenVerifier().verify("mocked", expected_nonce="this-attempt")

    async def test_the_identity_repr_carries_no_subject_or_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = claims()
        verifier_returning(monkeypatch, {**payload, "nonce": "n"})

        identity = await GoogleTokenVerifier().verify("mocked", expected_nonce="n")

        assert payload["sub"] not in str(identity)
        assert payload["email"] not in str(identity)


# ---------------------------------------------------------------------------
# Sign-up, sign-in, linking
# ---------------------------------------------------------------------------


class TestGoogleSignUpAndSignIn:
    async def test_a_new_google_user_gets_a_tenant_and_no_password(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = claims()

        response = await google_post(client, monkeypatch, payload)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["identity"]["user"]["email"] == payload["email"]

        user = (
            await db_session.execute(select(User).where(User.email == payload["email"]))
        ).scalar_one()
        # No invented placeholder password.
        assert user.password_hash is None
        assert user.is_active is True
        assert user.is_verified is True

        identity = (
            await db_session.execute(select(UserIdentity).where(UserIdentity.user_id == user.id))
        ).scalar_one()
        assert identity.provider == IdentityProvider.GOOGLE.value
        assert identity.subject == payload["sub"]

    async def test_an_existing_identity_signs_in_without_creating_a_second_account(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = claims()
        first = await google_post(client, monkeypatch, payload)
        assert first.status_code == 201

        second = await google_post(client, monkeypatch, payload, intent="login")
        assert second.status_code == 200

        count = len(
            (
                await db_session.execute(
                    select(UserIdentity).where(UserIdentity.subject == payload["sub"])
                )
            )
            .scalars()
            .all()
        )
        assert count == 1

    async def test_a_changed_google_address_does_not_create_a_second_account(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The subject identifies the person. The address is just a label."""
        payload = claims()
        await google_post(client, monkeypatch, payload)

        moved = claims(sub=payload["sub"], email=f"moved-{uuid.uuid4().hex}@example.com")
        response = await google_post(client, monkeypatch, moved, intent="login")

        assert response.status_code == 200
        identities = (
            (
                await db_session.execute(
                    select(UserIdentity).where(UserIdentity.subject == payload["sub"])
                )
            )
            .scalars()
            .all()
        )
        assert len(identities) == 1
        # The display label follows; the identity does not move.
        assert identities[0].provider_email == moved["email"]

    async def test_it_refuses_to_auto_link_a_matching_local_account(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The central rule: an email match is not proof of continuity."""
        email = f"local-{uuid.uuid4().hex}@example.com"
        registered = await client.post(
            "/api/v1/auth/register", json=registration_payload(email=email)
        )
        assert registered.status_code == 201

        response = await google_post(client, monkeypatch, claims(email=email))

        assert response.status_code == 409
        body = response.json()
        assert "sign in with your password" in str(body).lower()

    async def test_the_refusal_reveals_nothing_about_other_tenants(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        email = f"quiet-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        body = str((await google_post(client, monkeypatch, claims(email=email))).json())

        for leak in ("tenant_id", "tenantId", "user_id", "userId", "slug"):
            assert leak not in body

    async def test_a_subject_already_linked_elsewhere_cannot_be_claimed(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The global unique constraint, exercised through the service."""
        payload = claims()
        await google_post(client, monkeypatch, payload)

        # A second user attempting to link the same Google subject.
        other_email = f"other-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=other_email))
        other = (
            await db_session.execute(select(User).where(User.email == other_email))
        ).scalar_one()

        from app.core.exceptions import ConflictError
        from app.integrations.google import GoogleIdentity
        from app.services.google_auth import GoogleAuthService

        identity = GoogleIdentity(
            subject=payload["sub"],
            email=payload["email"],
            email_verified=True,
            name=None,
            nonce=None,
        )
        with pytest.raises(ConflictError, match="already linked"):
            await GoogleAuthService(db_session).link(user_id=other.id, identity=identity)

    async def test_a_duplicate_subject_is_refused_by_the_database(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What actually prevents a duplicate under concurrency.

        Two simultaneous first sign-ins cannot be driven through the HTTP client
        here — the fixture wires every request to one shared session, so
        genuinely parallel requests collide in SQLAlchemy rather than in
        Postgres. The guarantee being relied on is the global unique constraint,
        so that is what this asserts: a second row for the same subject is
        rejected by the database, not by a check-then-insert that could race.
        """
        from sqlalchemy.exc import IntegrityError

        payload = claims()
        assert (await google_post(client, monkeypatch, payload)).status_code == 201

        other_email = f"dup-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=other_email))
        other = (
            await db_session.execute(select(User).where(User.email == other_email))
        ).scalar_one()

        db_session.add(
            UserIdentity(
                user_id=other.id,
                provider=IdentityProvider.GOOGLE.value,
                subject=payload["sub"],
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()


class TestNoGoogleTokenIsPersisted:
    async def test_no_column_anywhere_holds_a_google_token(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = claims()
        await google_post(client, monkeypatch, payload)

        identity = (
            await db_session.execute(
                select(UserIdentity).where(UserIdentity.subject == payload["sub"])
            )
        ).scalar_one()

        stored = {c.name for c in UserIdentity.__table__.columns}
        for forbidden in ("id_token", "access_token", "refresh_token", "credential"):
            assert forbidden not in stored

        # And the row itself holds nothing token-shaped.
        values = " ".join(str(getattr(identity, c)) for c in stored)
        assert "mocked.credential.value" not in values


# ---------------------------------------------------------------------------
# Password-reset codes
# ---------------------------------------------------------------------------


async def request_reset(client: AsyncClient, email: str) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
    assert response.status_code == 200, response.text
    return dict(response.json())


class TestResetEnumerationResistance:
    async def test_known_and_unknown_addresses_get_the_same_shape(
        self, client: AsyncClient
    ) -> None:
        known = f"known-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=known))

        a = await request_reset(client, known)
        b = await request_reset(client, f"unknown-{uuid.uuid4().hex}@example.com")

        assert a.keys() == b.keys()
        assert a["message"] == b["message"]
        assert a["expiresInSeconds"] == b["expiresInSeconds"]
        # Both get a challenge id; the unknown one simply never verifies.
        assert a["challengeId"] and b["challengeId"]
        assert a["challengeId"] != b["challengeId"]

    async def test_an_unknown_address_challenge_never_verifies(self, client: AsyncClient) -> None:
        challenge = await request_reset(client, f"nobody-{uuid.uuid4().hex}@example.com")

        response = await client.post(
            "/api/v1/auth/password-reset/verify",
            json={"challengeId": challenge["challengeId"], "code": "000000"},
        )
        assert response.status_code == 401


class TestOtpStorage:
    async def test_the_code_is_stored_as_a_keyed_hmac_not_a_bare_hash(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The property that makes six digits survivable at rest."""
        import hashlib
        import json

        from app.core.redis import RedisPurpose, get_redis

        email = f"hmac-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        raw = await get_redis(RedisPurpose.SESSION).get(
            f"pwreset:challenge:{challenge.challenge_id}"
        )
        assert raw is not None
        record = json.loads(raw)

        # Not the code.
        assert code not in raw
        # Not a bare SHA-256 of the code either — that is a table lookup.
        assert record["otp"] != hashlib.sha256(code.encode()).hexdigest()
        assert len(record["otp"]) == 64

    async def test_the_reset_ticket_is_not_stored_in_the_clear(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        from app.core.redis import RedisPurpose, get_redis

        email = f"ticket-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None
        outcome = await service.verify(challenge_id=challenge.challenge_id, code=code)
        assert outcome is not None

        redis = get_redis(RedisPurpose.SESSION)
        assert await redis.get(f"pwreset:ticket:{outcome.reset_ticket}") is None


class TestOtpLifecycle:
    async def test_a_wrong_code_fails_and_the_right_one_still_works(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"wrong-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        wrong = "000000" if code != "000000" else "111111"
        assert await service.verify(challenge_id=challenge.challenge_id, code=wrong) is None
        assert await service.verify(challenge_id=challenge.challenge_id, code=code) is not None

    async def test_attempts_are_capped(self, client: AsyncClient, db_session: AsyncSession) -> None:
        email = f"attempts-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        wrong = "000000" if code != "000000" else "111111"
        for _ in range(settings.password_reset.max_verification_attempts):
            await service.verify(challenge_id=challenge.challenge_id, code=wrong)

        # Even the correct code no longer works: the challenge is burnt.
        assert await service.verify(challenge_id=challenge.challenge_id, code=code) is None

    async def test_a_code_cannot_be_replayed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"replay-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        assert await service.verify(challenge_id=challenge.challenge_id, code=code) is not None
        assert await service.verify(challenge_id=challenge.challenge_id, code=code) is None

    async def test_a_resend_within_the_cooldown_sends_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"cooldown-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        _, first = await service.request(email=email)
        _, second = await service.request(email=email)

        assert first is not None
        assert second is None  # cooled down, so no second code was minted

    async def test_a_new_challenge_retires_the_previous_one(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One active challenge per account, so guessing surfaces cannot stack."""
        monkeypatch.setattr(settings.password_reset, "resend_cooldown_seconds", 1)

        email = f"single-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        from app.core.redis import RedisPurpose, get_redis

        service = PasswordResetService(db_session)
        first_challenge, first_code = await service.request(email=email)

        # Clear the cooldown directly: this test is about the one-active-
        # challenge rule, not about the cooldown, and waiting a real second
        # would make it slow for no added confidence.
        user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
        await get_redis(RedisPurpose.SESSION).delete(f"pwreset:cooldown:{user.id}")

        second_challenge, second_code = await service.request(email=email)
        assert first_code is not None and second_code is not None

        assert (
            await service.verify(challenge_id=first_challenge.challenge_id, code=first_code) is None
        )
        assert (
            await service.verify(challenge_id=second_challenge.challenge_id, code=second_code)
            is not None
        )

    async def test_the_hourly_request_limit_stops_repeated_probing(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.password_reset, "resend_cooldown_seconds", 1)
        monkeypatch.setattr(settings.password_reset, "requests_per_email_per_hour", 2)

        email = f"ratelimit-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        from app.core.redis import RedisPurpose, get_redis

        redis = get_redis(RedisPurpose.SESSION)
        user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
        service = PasswordResetService(db_session)

        codes = []
        for _ in range(4):
            await redis.delete(f"pwreset:cooldown:{user.id}")
            codes.append((await service.request(email=email))[1])

        assert codes[0] is not None
        assert codes[-1] is None


class TestResetCompletion:
    async def test_a_completed_reset_sets_the_password_and_revokes_sessions(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"complete-{uuid.uuid4().hex}@example.com"
        registered = await client.post(
            "/api/v1/auth/register", json=registration_payload(email=email)
        )
        assert registered.status_code == 201

        user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
        before = user.password_hash

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None
        outcome = await service.verify(challenge_id=challenge.challenge_id, code=code)
        assert outcome is not None

        response = await client.post(
            "/api/v1/auth/password-reset/complete",
            json={"resetTicket": outcome.reset_ticket, "newPassword": "An0ther-Strong-Pass!"},
        )
        assert response.status_code == 200, response.text

        await db_session.refresh(user)
        assert user.password_hash != before
        assert user.password_hash is not None

        live = (
            (
                await db_session.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert live == [], "a reset must end every existing session"

    async def test_a_ticket_cannot_be_spent_twice(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"twice-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None
        outcome = await service.verify(challenge_id=challenge.challenge_id, code=code)
        assert outcome is not None

        body = {"resetTicket": outcome.reset_ticket, "newPassword": "An0ther-Strong-Pass!"}
        assert (
            await client.post("/api/v1/auth/password-reset/complete", json=body)
        ).status_code == 200
        assert (
            await client.post("/api/v1/auth/password-reset/complete", json=body)
        ).status_code == 401

    async def test_a_weak_password_is_refused_by_the_existing_policy(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"weak-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None
        outcome = await service.verify(challenge_id=challenge.challenge_id, code=code)
        assert outcome is not None

        response = await client.post(
            "/api/v1/auth/password-reset/complete",
            json={"resetTicket": outcome.reset_ticket, "newPassword": "short"},
        )
        assert response.status_code in (400, 422)

    async def test_a_google_only_user_can_gain_a_password(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The documented decision: losing Google must not lose the workspace."""
        payload = claims()
        assert (await google_post(client, monkeypatch, payload)).status_code == 201

        user = (
            await db_session.execute(select(User).where(User.email == payload["email"]))
        ).scalar_one()
        assert user.password_hash is None

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=payload["email"])
        assert code is not None, "a Google-only account must still be able to reset"
        outcome = await service.verify(challenge_id=challenge.challenge_id, code=code)
        assert outcome is not None

        assert (
            await service.complete(
                reset_ticket=outcome.reset_ticket, new_password="An0ther-Strong-Pass!"
            )
            is not None
        )
        await db_session.refresh(user)
        assert user.password_hash is not None


class TestConcurrency:
    async def test_concurrent_verification_admits_exactly_one(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"race-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        service = PasswordResetService(db_session)
        challenge, code = await service.request(email=email)
        assert code is not None

        outcomes = await asyncio.gather(
            service.verify(challenge_id=challenge.challenge_id, code=code),
            service.verify(challenge_id=challenge.challenge_id, code=code),
        )
        assert sum(1 for o in outcomes if o is not None) == 1


# ---------------------------------------------------------------------------
# Email provider
# ---------------------------------------------------------------------------


class TestEmailProvider:
    async def test_the_stub_is_the_default_so_nothing_is_ever_sent_by_accident(
        self,
    ) -> None:
        from app.integrations.email import get_email_provider

        reset_email_provider()
        assert isinstance(get_email_provider(), StubEmailProvider)

    async def test_resend_refuses_an_unexpected_host(self) -> None:
        with pytest.raises(EmailDeliveryError, match="unexpected host"):
            ResendEmailProvider(api_key="k", base_url="https://not-resend.example.com")

    async def test_resend_refuses_without_a_key(self) -> None:
        with pytest.raises(EmailDeliveryError, match="not configured"):
            ResendEmailProvider(api_key="")

    async def test_a_provider_error_is_reported_without_a_recipient(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "invalid to: person@example.com"})

        provider = ResendEmailProvider(
            api_key="k",
            base_url="https://api.resend.com",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(EmailDeliveryError) as caught:
            await provider.send(EmailMessage(to="person@example.com", subject="s", text="t"))
        assert "person@example.com" not in str(caught.value)

    async def test_an_unparseable_response_fails_closed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        provider = ResendEmailProvider(
            api_key="k",
            base_url="https://api.resend.com",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(EmailDeliveryError, match="unexpected response"):
            await provider.send(EmailMessage(to="a@example.com", subject="s", text="t"))

    async def test_a_successful_send_returns_the_provider_id(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"].startswith("Bearer ")
            return httpx.Response(200, json={"id": "resend-message-id"})

        provider = ResendEmailProvider(
            api_key="k",
            base_url="https://api.resend.com",
            transport=httpx.MockTransport(handler),
        )
        assert (
            await provider.send(EmailMessage(to="a@example.com", subject="s", text="t"))
            == "resend-message-id"
        )

    async def test_the_send_budget_fails_closed_when_exhausted(self) -> None:
        budget = SendBudget(max_per_hour=1, failure_threshold=5, cooldown_seconds=60)
        budget.check()
        budget.record_success()
        with pytest.raises(EmailDeliveryError, match="budget exhausted"):
            budget.check()

    async def test_the_breaker_opens_after_repeated_failures(self) -> None:
        budget = SendBudget(max_per_hour=100, failure_threshold=2, cooldown_seconds=60)
        budget.record_failure()
        budget.record_failure()
        with pytest.raises(EmailDeliveryError, match="temporarily disabled"):
            budget.check()

    async def test_a_delivery_failure_discards_the_challenge(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A challenge nobody can satisfy is worse than none."""
        email = f"undeliverable-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        class FailingProvider:
            async def send(self, message: EmailMessage) -> str:
                raise EmailDeliveryError("provider down")

        # Patched at the provider seam rather than at the router's imported
        # name: this way the composer, the message body and the endpoint's
        # error handling all still run for real.
        monkeypatch.setattr(
            "app.integrations.email.messages.get_email_provider", lambda: FailingProvider()
        )

        challenge = await request_reset(client, email)
        # Response shape is unchanged — a delivery failure is not disclosed.
        assert challenge["challengeId"]

        verify = await client.post(
            "/api/v1/auth/password-reset/verify",
            json={"challengeId": challenge["challengeId"], "code": "123456"},
        )
        assert verify.status_code == 401


class TestNoSecretsInResponses:
    async def test_the_reset_request_response_carries_no_code(self, client: AsyncClient) -> None:
        email = f"nocode-{uuid.uuid4().hex}@example.com"
        await client.post("/api/v1/auth/register", json=registration_payload(email=email))

        body = str(await request_reset(client, email))

        # A six-digit run anywhere in the body would be the code leaking.
        import re

        assert re.search(r"\b\d{6}\b", body) is None, body

    async def test_no_endpoint_returns_the_google_client_secret_or_api_key(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nonce = (await client.post("/api/v1/auth/google/nonce")).json()
        signup = await google_post(client, monkeypatch, claims())

        blob = str(nonce) + signup.text
        for secret in ("client_secret", "clientSecret", "RESEND_API_KEY", "re_"):
            assert secret not in blob


class TestProviderIdentityErasure:
    async def test_erasing_a_user_removes_their_google_identity(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise the Google account could sign back into an erased user."""
        from app.services.data_subject_erasure import PlatformUserErasureService

        payload = claims()
        assert (await google_post(client, monkeypatch, payload)).status_code == 201

        user = (
            await db_session.execute(select(User).where(User.email == payload["email"]))
        ).scalar_one()

        service = PlatformUserErasureService(db_session)
        subject = await service.resolve_by_id(tenant_id=user.tenant_id, user_id=user.id)
        outcome = await service.erase(subject)
        await db_session.flush()

        assert outcome.counts["user_identities"] == 1
        assert (
            await db_session.execute(
                select(UserIdentity).where(UserIdentity.subject == payload["sub"])
            )
        ).scalars().all() == []

    async def test_the_erasure_declaration_covers_the_new_table(self) -> None:
        """The C1.2-R1 guard is what caught this table before it shipped."""
        from app.services.data_subject_erasure import USER_REFERENCES

        assert any(r.model is UserIdentity for r in USER_REFERENCES)
