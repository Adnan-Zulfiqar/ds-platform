"""Track E5 — platform and tenant tokens are not interchangeable (D-015)."""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import AuthenticationError
from app.core.tokens import (
    TokenType,
    create_access_token,
    create_platform_token,
    decode_platform_token,
    decode_token,
)

pytestmark = pytest.mark.unit


def test_a_platform_token_round_trips() -> None:
    admin_id = uuid.uuid4()
    claims = decode_platform_token(create_platform_token(admin_id=admin_id).token)
    assert claims.admin_id == admin_id


def test_a_tenant_owner_token_is_refused_by_the_platform() -> None:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=("owner",)
    ).token
    with pytest.raises(AuthenticationError):
        decode_platform_token(token)


def test_a_platform_token_is_refused_by_the_tenant_api() -> None:
    token = create_platform_token(admin_id=uuid.uuid4()).token
    with pytest.raises(AuthenticationError):
        decode_token(token, expected_type=TokenType.ACCESS)
