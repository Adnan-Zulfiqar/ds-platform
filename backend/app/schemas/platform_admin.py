"""Platform operator schemas (Track E5). The TOTP secret and password hash are
never part of any response."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, SecretStr

from app.schemas.base import CamelCaseModel


class PlatformLoginRequest(CamelCaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr
    code: str = Field(min_length=6, max_length=8)


class PlatformLoginResponse(CamelCaseModel):
    access_token: str
    expires_at: datetime
    token_type: str = "bearer"  # noqa: S105 — the OAuth token-type label, not a credential


class PlatformAdminRead(CamelCaseModel):
    id: uuid.UUID
    email: str
    last_login_at: datetime | None


__all__ = ["PlatformAdminRead", "PlatformLoginRequest", "PlatformLoginResponse"]
