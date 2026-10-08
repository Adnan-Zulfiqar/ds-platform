"""Track E5 — platform and tenant tokens are not interchangeable (D-015), and
a platform token names its session (D-018)."""

from __future__ import annotations

import uuid

import jwt
import pytest

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.core.tokens import (
    TokenType,
    create_access_token,
    create_platform_token,
    decode_platform_token,
    decode_token,
    platform_token_expiry,
)

pytestmark = pytest.mark.unit


def _token(admin_id: uuid.UUID | None = None, session_id: uuid.UUID | None = None) -> str:
    return create_platform_token(
        admin_id=admin_id or uuid.uuid4(),
        session_id=session_id or uuid.uuid4(),
        expires_at=platform_token_expiry(),
    ).token


def test_a_platform_token_round_trips_with_its_session() -> None:
    admin_id, session_id = uuid.uuid4(), uuid.uuid4()
    claims = decode_platform_token(_token(admin_id, session_id))
    assert claims.admin_id == admin_id
    assert claims.session_id == session_id


def test_a_platform_token_without_a_session_is_refused() -> None:
    """Tokens issued before D-018 carry no session and cannot be revoked, so
    they are not accepted at all."""
    payload = jwt.decode(
        _token(),
        settings.security.secret_key.get_secret_value(),
        algorithms=[settings.security.jwt_algorithm],
        options={"verify_aud": False},
    )
    del payload["sid"]
    forged = jwt.encode(
        payload,
        settings.security.secret_key.get_secret_value(),
        algorithm=settings.security.jwt_algorithm,
    )
    with pytest.raises(AuthenticationError):
        decode_platform_token(forged)


def test_a_tenant_owner_token_is_refused_by_the_platform() -> None:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=("owner",)
    ).token
    with pytest.raises(AuthenticationError):
        decode_platform_token(token)


def test_a_platform_token_is_refused_by_the_tenant_api() -> None:
    with pytest.raises(AuthenticationError):
        decode_token(_token(), expected_type=TokenType.ACCESS)
