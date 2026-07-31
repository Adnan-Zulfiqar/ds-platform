"""Email verification foundation (H4) — no fake SMTP."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.deps import require_verified
from app.core.context import AuthenticatedUser, set_tenant_id
from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.models.user import User
from app.services.email_verification import (
    EmailVerificationService,
    EmailVerificationTokenRepository,
)

pytestmark = pytest.mark.unit


def _principal(*, verified: bool) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="user@example.com",
        roles=frozenset({"owner"}),
        is_verified=verified,
    )


class TestRequireVerified:
    def test_is_noop_when_enforcement_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.api.deps.settings.security.require_email_verification",
            False,
        )
        principal = _principal(verified=False)
        assert require_verified(principal) is principal

    def test_rejects_unverified_when_enforcement_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.api.deps.settings.security.require_email_verification",
            True,
        )
        with pytest.raises(PermissionDeniedError):
            require_verified(_principal(verified=False))

    def test_admits_verified_when_enforcement_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.api.deps.settings.security.require_email_verification",
            True,
        )
        principal = _principal(verified=True)
        assert require_verified(principal) is principal


class TestEmailVerificationService:
    @pytest.mark.asyncio
    async def test_request_rejects_already_verified_users(self) -> None:
        session = MagicMock()
        service = EmailVerificationService(session)
        user = MagicMock(spec=User)
        user.is_verified = True
        user.email = "a@example.com"
        with pytest.raises(ValidationError):
            await service.request_for_user(user)

    @pytest.mark.asyncio
    async def test_confirm_rejects_foreign_user_token(
        self, monkeypatch: pytest.MonkeyPatch, tenant_id: uuid.UUID
    ) -> None:
        set_tenant_id(tenant_id)
        session = MagicMock()
        service = EmailVerificationService(session)

        record = MagicMock()
        record.user_id = uuid.uuid4()
        record.expires_at = datetime.now(UTC) + timedelta(hours=1)
        service.tokens.get_by_hash = AsyncMock(return_value=record)  # type: ignore[method-assign]

        user = MagicMock(spec=User)
        user.id = uuid.uuid4()
        with pytest.raises(NotFoundError):
            await service.confirm(raw_token="not-the-right-token-value", user=user)


class TestEmailVerificationTokenScoping:
    def test_base_query_includes_tenant_filter(self, tenant_id: uuid.UUID) -> None:
        from sqlalchemy.dialects import postgresql

        set_tenant_id(tenant_id)
        repo = EmailVerificationTokenRepository(MagicMock())
        sql = str(
            repo._base_query().compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "email_verification_tokens.tenant_id" in sql
        assert str(tenant_id) in sql
        assert "deleted_at IS NULL" in sql
