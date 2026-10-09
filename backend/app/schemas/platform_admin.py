"""Platform operator schemas (Track E5, D-018). The TOTP secret, password
hash and session tokens are never part of any response."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, SecretStr

from app.core.platform_permissions import PlatformRole
from app.schemas.base import CamelCaseModel


class PlatformLoginRequest(CamelCaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr
    code: str = Field(min_length=6, max_length=8)


class PlatformLoginResponse(CamelCaseModel):
    access_token: str
    expires_at: datetime
    token_type: str = "bearer"  # noqa: S105 — the OAuth token-type label, not a credential


class PlatformReauthRequest(CamelCaseModel):
    password: SecretStr
    code: str = Field(min_length=6, max_length=8)


class PlatformReauthResponse(CamelCaseModel):
    reauthenticated_at: datetime
    valid_until: datetime


class PlatformSessionRead(CamelCaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None
    reauthenticated_at: datetime | None
    client_ip: str | None
    user_agent: str | None
    #: The session this request is using.
    current: bool = False


class PlatformAdminRead(CamelCaseModel):
    """The signed-in operator. ``permissions`` is what the server enforces;
    the console uses it only to hide what would be refused anyway."""

    id: uuid.UUID
    email: str
    role: str
    permissions: list[str]
    last_login_at: datetime | None
    session: PlatformSessionRead
    #: When the current re-authentication stops counting, if there is one.
    reauth_valid_until: datetime | None


class PlatformOperatorRead(CamelCaseModel):
    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None
    open_sessions: int


class PlatformActionReason(CamelCaseModel):
    """Every access-changing action says why; the reason is audited."""

    reason: str = Field(min_length=3, max_length=500)


#: Kept under its E5b name for the workspace routes.
PlatformTenantStateChange = PlatformActionReason


class PlatformOperatorRoleChange(PlatformActionReason):
    role: PlatformRole


class PlatformSessionsRevoked(CamelCaseModel):
    sessions_ended: int


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


class PlatformAuditEntryRead(CamelCaseModel):
    """One audit row with the operator's email, for the audit centre."""

    id: uuid.UUID
    created_at: datetime
    admin_id: uuid.UUID | None
    admin_email: str | None
    actor_role: str | None
    action: str
    outcome: str
    target_tenant_id: uuid.UUID | None
    target_type: str | None
    target_id: str | None
    detail: dict[str, Any]
    client_ip: str | None
    user_agent: str | None
    request_id: str | None


class SecuritySummaryRead(CamelCaseModel):
    window_hours: int
    by_action: dict[str, int]
    top_ips: list[dict[str, Any]]


class PlatformAuditRead(CamelCaseModel):
    id: uuid.UUID
    created_at: datetime
    admin_id: uuid.UUID | None
    actor_role: str | None
    action: str
    outcome: str
    target_tenant_id: uuid.UUID | None
    target_type: str | None
    target_id: str | None
    detail: dict[str, Any]
    client_ip: str | None
    user_agent: str | None
    request_id: str | None


__all__ = [
    "PlatformActionReason",
    "PlatformAdminRead",
    "PlatformAuditEntryRead",
    "PlatformAuditRead",
    "PlatformLoginRequest",
    "PlatformLoginResponse",
    "PlatformOperatorRead",
    "PlatformOperatorRoleChange",
    "PlatformReauthRequest",
    "PlatformReauthResponse",
    "PlatformSessionRead",
    "PlatformSessionsRevoked",
    "PlatformTenantHealthRead",
    "PlatformTenantRead",
    "PlatformTenantStateChange",
    "SecuritySummaryRead",
]
