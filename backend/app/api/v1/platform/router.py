"""Platform operator endpoints (Track E5, decision D-015).

Every route here is behind :func:`require_platform_network`, which answers
404 unless ``PLATFORM_ADMIN_ALLOWED_CIDRS`` is set and the caller is inside
it. Every route except sign-in also requires a platform token. Tenant tokens
are refused by audience, so no tenant role ever reaches these handlers.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import DbSession, PlatformNetwork, RequirePlatformAdmin, endpoint_rate_limit
from app.core.client_ip import resolve_client_ip
from app.schemas.common import Page
from app.schemas.platform_admin import (
    PlatformAdminRead,
    PlatformAuditRead,
    PlatformLoginRequest,
    PlatformLoginResponse,
    PlatformTenantRead,
    PlatformTenantStateChange,
)
from app.services.platform_admin import PlatformAdminService

router = APIRouter(prefix="/platform", tags=["platform"], dependencies=[PlatformNetwork])

#: Counted per client address (there is no principal yet). Tight: a real
#: operator signs in a few times a day.
_login_limit = endpoint_rate_limit("platform-login", limit=10, window_seconds=900)


@router.post(
    "/auth/login",
    response_model=PlatformLoginResponse,
    summary="Platform operator sign-in (password + one-time code)",
    dependencies=[Depends(_login_limit)],
)
async def platform_login(
    payload: PlatformLoginRequest, request: Request, session: DbSession
) -> PlatformLoginResponse:
    issued = await PlatformAdminService(session).authenticate(
        email=payload.email,
        password=payload.password.get_secret_value(),
        code=payload.code,
        client_ip=resolve_client_ip(request),
    )
    return PlatformLoginResponse(access_token=issued.token, expires_at=issued.expires_at)


@router.get("/me", response_model=PlatformAdminRead, summary="The signed-in operator")
async def platform_me(admin: RequirePlatformAdmin) -> PlatformAdminRead:
    return PlatformAdminRead.model_validate(admin)


# --- Workspaces (E5b) ---------------------------------------------------------


@router.get("/tenants", response_model=Page[PlatformTenantRead], summary="All workspaces")
async def platform_tenants(
    session: DbSession,
    _admin: RequirePlatformAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 25,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[PlatformTenantRead]:
    rows, total = await PlatformAdminService(session).list_tenants(page=page, size=size, q=q)
    return Page[PlatformTenantRead].build(
        items=[PlatformTenantRead.model_validate(r, from_attributes=True) for r in rows],
        page=page,
        size=size,
        total_items=total,
    )


async def _set_active(
    tenant_id: uuid.UUID,
    payload: PlatformTenantStateChange,
    request: Request,
    session: DbSession,
    admin: RequirePlatformAdmin,
    *,
    active: bool,
) -> PlatformTenantRead:
    row = await PlatformAdminService(session).set_tenant_active(
        tenant_id,
        active=active,
        reason=payload.reason,
        admin=admin,
        client_ip=resolve_client_ip(request),
    )
    return PlatformTenantRead.model_validate(row, from_attributes=True)


@router.post(
    "/tenants/{tenant_id}/suspend",
    response_model=PlatformTenantRead,
    summary="Suspend a workspace (audited)",
)
async def platform_suspend_tenant(
    tenant_id: uuid.UUID,
    payload: PlatformTenantStateChange,
    request: Request,
    session: DbSession,
    admin: RequirePlatformAdmin,
) -> PlatformTenantRead:
    return await _set_active(tenant_id, payload, request, session, admin, active=False)


@router.post(
    "/tenants/{tenant_id}/reactivate",
    response_model=PlatformTenantRead,
    summary="Reactivate a workspace (audited)",
)
async def platform_reactivate_tenant(
    tenant_id: uuid.UUID,
    payload: PlatformTenantStateChange,
    request: Request,
    session: DbSession,
    admin: RequirePlatformAdmin,
) -> PlatformTenantRead:
    return await _set_active(tenant_id, payload, request, session, admin, active=True)


@router.get("/audit", response_model=list[PlatformAuditRead], summary="Recent operator actions")
async def platform_audit(
    session: DbSession,
    _admin: RequirePlatformAdmin,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[PlatformAuditRead]:
    rows = await PlatformAdminService(session).recent_audit(limit=limit)
    return [PlatformAuditRead.model_validate(r, from_attributes=True) for r in rows]
