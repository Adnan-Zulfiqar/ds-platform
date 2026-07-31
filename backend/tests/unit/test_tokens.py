"""Tests for JWT issuing and verification.

These cover the properties that a forged or misused token must fail on. They run
without a database because token verification is pure computation — which is the
point of stateless tokens, and means these checks run on every commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import settings
from app.core.exceptions import AuthenticationError, TokenExpiredError
from app.core.tokens import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


class TestAccessTokens:
    def test_round_trips_identity_claims(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        issued = create_access_token(user_id=user_id, tenant_id=tenant_id, roles=("admin",))
        claims = decode_token(issued.token, expected_type=TokenType.ACCESS)

        assert claims.user_id == user_id
        assert claims.tenant_id == tenant_id
        assert claims.roles == ("admin",)
        assert claims.is_access
        assert claims.is_verified is True

    def test_access_token_carries_unverified_flag(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        issued = create_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            is_verified=False,
        )
        claims = decode_token(issued.token, expected_type=TokenType.ACCESS)
        assert claims.is_verified is False

    def test_each_token_has_a_unique_jti(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Two tokens minted back to back must be distinguishable.

        Without this, two refresh tokens issued in the same second would hash
        identically and collide on the unique constraint.
        """
        first = create_access_token(user_id=user_id, tenant_id=tenant_id)
        second = create_access_token(user_id=user_id, tenant_id=tenant_id)

        assert first.jti != second.jti
        assert first.token != second.token

    def test_expiry_reflects_configured_ttl(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        issued = create_access_token(user_id=user_id, tenant_id=tenant_id)
        expected = datetime.now(UTC) + timedelta(minutes=settings.security.access_token_ttl_minutes)

        assert abs((issued.expires_at - expected).total_seconds()) < 5


class TestRejection:
    def test_expired_token_raises_token_expired(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        """Expiry is distinguishable from other failures.

        The client needs to tell "refresh me" apart from "sign in again"; every
        other failure is deliberately opaque.
        """
        past = datetime.now(UTC) - timedelta(hours=1)
        payload = {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "typ": TokenType.ACCESS.value,
            "jti": uuid.uuid4().hex,
            "iat": int((past - timedelta(minutes=30)).timestamp()),
            "exp": int(past.timestamp()),
            "iss": settings.security.jwt_issuer,
            "aud": settings.security.jwt_audience,
            "roles": [],
        }
        token = jwt.encode(
            payload,
            settings.security.secret_key.get_secret_value(),
            algorithm=settings.security.jwt_algorithm,
        )

        with pytest.raises(TokenExpiredError):
            decode_token(token, expected_type=TokenType.ACCESS)

    def test_refresh_token_is_rejected_as_an_access_token(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        """The single most important check in this module.

        Without the `typ` claim being verified, a refresh token would work as a
        bearer credential on every endpoint — turning the long-lived, rotatable
        credential into a permanent API key and defeating rotation entirely.
        """
        refresh = create_refresh_token(user_id=user_id, tenant_id=tenant_id)

        with pytest.raises(AuthenticationError):
            decode_token(refresh.token, expected_type=TokenType.ACCESS)

    def test_access_token_is_rejected_as_a_refresh_token(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        access = create_access_token(user_id=user_id, tenant_id=tenant_id)

        with pytest.raises(AuthenticationError):
            decode_token(access.token, expected_type=TokenType.REFRESH)

    def test_token_signed_with_another_key_is_rejected(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "typ": TokenType.ACCESS.value,
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "iss": settings.security.jwt_issuer,
            "aud": settings.security.jwt_audience,
        }
        forged = jwt.encode(payload, "an-attacker-chosen-key", algorithm="HS256")

        with pytest.raises(AuthenticationError):
            decode_token(forged, expected_type=TokenType.ACCESS)

    def test_tampered_payload_is_rejected(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
    ) -> None:
        """Editing the tenant claim must invalidate the signature.

        This is the attack that would otherwise grant cross-tenant access with
        a legitimately issued token.
        """
        issued = create_access_token(user_id=user_id, tenant_id=tenant_id)
        header, _payload, signature = issued.token.split(".")

        forged_payload = jwt.encode(
            {"tid": str(other_tenant_id)}, "irrelevant", algorithm="HS256"
        ).split(".")[1]
        tampered = f"{header}.{forged_payload}.{signature}"

        with pytest.raises(AuthenticationError):
            decode_token(tampered, expected_type=TokenType.ACCESS)

    def test_token_for_another_audience_is_rejected(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        """A token minted for a different service must not be accepted here.

        Relevant as soon as one signing key covers more than one service.
        """
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "typ": TokenType.ACCESS.value,
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "iss": settings.security.jwt_issuer,
            "aud": "some-other-service",
        }
        token = jwt.encode(
            payload,
            settings.security.secret_key.get_secret_value(),
            algorithm=settings.security.jwt_algorithm,
        )

        with pytest.raises(AuthenticationError):
            decode_token(token, expected_type=TokenType.ACCESS)

    def test_token_missing_required_claims_is_rejected(self, user_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(user_id),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
                "iss": settings.security.jwt_issuer,
                "aud": settings.security.jwt_audience,
            },
            settings.security.secret_key.get_secret_value(),
            algorithm=settings.security.jwt_algorithm,
        )

        with pytest.raises(AuthenticationError):
            decode_token(token, expected_type=TokenType.ACCESS)

    @pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c", "Bearer something", "..."])
    def test_malformed_input_is_rejected(self, garbage: str) -> None:
        with pytest.raises(AuthenticationError):
            decode_token(garbage, expected_type=TokenType.ACCESS)


class TestRefreshTokens:
    def test_carries_no_roles(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Roles belong only in access tokens.

        A refresh token lives for weeks; embedding roles in it would let a
        stale privilege set survive far longer than the access TTL bounds.
        """
        issued = create_refresh_token(user_id=user_id, tenant_id=tenant_id)
        claims = decode_token(issued.token, expected_type=TokenType.REFRESH)

        assert claims.roles == ()

    def test_lives_longer_than_an_access_token(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        access = create_access_token(user_id=user_id, tenant_id=tenant_id)
        refresh = create_refresh_token(user_id=user_id, tenant_id=tenant_id)

        assert refresh.expires_at > access.expires_at
