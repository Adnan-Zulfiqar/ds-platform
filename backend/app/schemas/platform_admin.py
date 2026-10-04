"""Platform operator schemas (Track E5). The TOTP secret and password hash are
never part of any response."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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


class PlatformTenantRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    is_active: bool
    created_at: datetime
    users: int
    connected_stores: int


class PlatformTenantHealthRead(CamelCaseModel):
    tenant_id: uuid.UUID
    window_hours: int
    failed_order_syncs: int
    failed_inventory_syncs: int
    listings_in_error: int
    failed_notification_emails: int


class PlatformTenantStateChange(CamelCaseModel):
    """Every suspension or reactivation says why; the reason is audited."""

    reason: str = Field(min_length=3, max_length=500)


class PlatformAuditRead(CamelCaseModel):
    id: uuid.UUID
    created_at: datetime
    admin_id: uuid.UUID | None
    action: str
    target_tenant_id: uuid.UUID | None
    detail: dict[str, Any]
    client_ip: str | None


__all__ = [
    "PlatformAdminRead",
    "PlatformAuditRead",
    "PlatformLoginRequest",
    "PlatformLoginResponse",
    "PlatformTenantHealthRead",
    "PlatformTenantRead",
    "PlatformTenantStateChange",
]
