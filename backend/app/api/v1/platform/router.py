"""Platform operator endpoints (Track E5, D-015; Admin Control Center, D-018).

Every route here is behind :func:`require_platform_network`, which answers
404 unless ``PLATFORM_ADMIN_ALLOWED_CIDRS`` is set and the caller is inside
it. Every route except sign-in also requires a platform token whose session
is still open. Tenant tokens are refused by audience, so no tenant role ever
reaches these handlers.

Each route names its permission in its dependencies; actions that change
access additionally need a recent re-authentication. The checks are
dependencies, never handler code (CLAUDE.md §7.8).
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response

from app.api.deps import (
    DbSession,
    PlatformAudit,
    PlatformNetwork,
    RequirePlatformAdmin,
    platform_rate_limit,
    require_platform_permission,
    require_platform_reauth,
)
from app.api.v1.platform.console import router as console_router
from app.api.v1.platform.workspace_data import router as workspace_data_router
from app.core.platform_permissions import REAUTH_WINDOW_MINUTES, PlatformPermission
from app.models.platform_admin import PlatformAdmin, PlatformAdminSession
from app.schemas.common import Page
from app.schemas.platform_admin import (
    PlatformActionReason,
    PlatformAdminRead,
    PlatformAuditEntryRead,
    PlatformAuditRead,
    PlatformLoginRequest,
    PlatformLoginResponse,
    PlatformOperatorRead,
    PlatformOperatorRoleChange,
    PlatformReauthRequest,
    PlatformReauthResponse,
    PlatformSessionRead,
    PlatformSessionsRevoked,
    PlatformTenantHealthRead,
    PlatformTenantRead,
    PlatformTenantStateChange,
    SecuritySummaryRead,
)
from app.services.platform_admin import PlatformAdminService, PlatformPrincipal

router = APIRouter(prefix="/platform", tags=["platform"], dependencies=[PlatformNetwork])

#: Counted per client address (there is no principal yet). Tight: a real
#: operator signs in a few times a day.
_login_limit = platform_rate_limit("platform-login", limit=10, window_seconds=900)
#: Re-authentication guesses the same secrets as sign-in, so it is as tight.
_reauth_limit = platform_rate_limit("platform-reauth", limit=10, window_seconds=900)

TenantsRead = Annotated[
    PlatformPrincipal, Depends(require_platform_permission(PlatformPermission.TENANTS_READ))
]
TenantsSuspend = Annotated[
    PlatformPrincipal, Depends(require_platform_reauth(PlatformPermission.TENANTS_SUSPEND))
]
OperatorsRead = Annotated[
    PlatformPrincipal, Depends(require_platform_permission(PlatformPermission.OPERATORS_READ))
]
OperatorsManage = Annotated[
    PlatformPrincipal, Depends(require_platform_reauth(PlatformPermission.OPERATORS_MANAGE))
]
AuditRead = Annotated[
    PlatformPrincipal, Depends(require_platform_permission(PlatformPermission.AUDIT_READ))
]


def _session_read(row: PlatformAdminSession, current: uuid.UUID) -> PlatformSessionRead:
    read = PlatformSessionRead.model_validate(row, from_attributes=True)
    return read.model_copy(update={"current": row.id == current})


def _operator_read(admin: PlatformAdmin, open_sessions: int) -> PlatformOperatorRead:
    return PlatformOperatorRead(
        id=admin.id,
        email=admin.email,
        role=admin.role,
        is_active=admin.is_active,
        created_at=admin.created_at,
        last_login_at=admin.last_login_at,
        open_sessions=open_sessions,
    )


# --- Sign-in and the operator's own sessions ----------------------------------


@router.post(
    "/auth/login",
    response_model=PlatformLoginResponse,
    summary="Platform operator sign-in (password + one-time code)",
    dependencies=[Depends(_login_limit)],
)
async def platform_login(
    payload: PlatformLoginRequest, session: DbSession, ctx: PlatformAudit
) -> PlatformLoginResponse:
    issued = await PlatformAdminService(session).authenticate(
        email=payload.email,
        password=payload.password.get_secret_value(),
        code=payload.code,
        ctx=ctx,
    )
    return PlatformLoginResponse(access_token=issued.token, expires_at=issued.expires_at)


@router.get("/me", response_model=PlatformAdminRead, summary="The signed-in operator")
async def platform_me(principal: RequirePlatformAdmin) -> PlatformAdminRead:
    admin, current = principal.admin, principal.session
    reauth_until = (
        current.reauthenticated_at + timedelta(minutes=REAUTH_WINDOW_MINUTES)
        if current.reauthenticated_at is not None and principal.reauthenticated_recently()
        else None
    )
    return PlatformAdminRead(
        id=admin.id,
        email=admin.email,
        role=admin.role,
        permissions=sorted(p.value for p in principal.permissions),
        last_login_at=admin.last_login_at,
        session=_session_read(current, current.id),
        reauth_valid_until=reauth_until,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def platform_logout(
    principal: RequirePlatformAdmin, session: DbSession, ctx: PlatformAudit
) -> None:
    await PlatformAdminService(session).logout(principal, ctx)


@router.post(
    "/auth/reauth",
    response_model=PlatformReauthResponse,
    summary="Confirm password and code again for sensitive actions",
    dependencies=[Depends(_reauth_limit)],
)
async def platform_reauth(
    payload: PlatformReauthRequest,
    principal: RequirePlatformAdmin,
    session: DbSession,
    ctx: PlatformAudit,
) -> PlatformReauthResponse:
    at = await PlatformAdminService(session).reauthenticate(
        principal, password=payload.password.get_secret_value(), code=payload.code, ctx=ctx
    )
    return PlatformReauthResponse(
        reauthenticated_at=at, valid_until=at + timedelta(minutes=REAUTH_WINDOW_MINUTES)
    )


@router.get(
    "/auth/sessions", response_model=list[PlatformSessionRead], summary="Your open sessions"
)
async def platform_my_sessions(
    principal: RequirePlatformAdmin, session: DbSession
) -> list[PlatformSessionRead]:
    rows = await PlatformAdminService(session).my_sessions(principal)
    return [_session_read(r, principal.session.id) for r in rows]


@router.post(
    "/auth/sessions/{session_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End one of your sessions (another device)",
)
async def platform_revoke_my_session(
    session_id: uuid.UUID,
    principal: RequirePlatformAdmin,
    session: DbSession,
    ctx: PlatformAudit,
) -> None:
    await PlatformAdminService(session).revoke_own_session(principal, session_id, ctx)


# --- Operators (D-018) ----------------------------------------------------------


@router.get("/operators", response_model=list[PlatformOperatorRead], summary="All operators")
async def platform_operators(
    session: DbSession, _principal: OperatorsRead
) -> list[PlatformOperatorRead]:
    rows = await PlatformAdminService(session).operators()
    return [_operator_read(admin, count) for admin, count in rows]


@router.get(
    "/operators/{operator_id}/sessions",
    response_model=list[PlatformSessionRead],
    summary="An operator's open sessions",
)
async def platform_operator_sessions(
    operator_id: uuid.UUID, session: DbSession, principal: OperatorsRead
) -> list[PlatformSessionRead]:
    rows = await PlatformAdminService(session).operator_sessions(operator_id)
    return [_session_read(r, principal.session.id) for r in rows]


@router.post(
    "/operators/{operator_id}/role",
    response_model=PlatformOperatorRead,
    summary="Change an operator's role (re-auth, audited)",
)
async def platform_set_operator_role(
    operator_id: uuid.UUID,
    payload: PlatformOperatorRoleChange,
    session: DbSession,
    principal: OperatorsManage,
    ctx: PlatformAudit,
) -> PlatformOperatorRead:
    admin = await PlatformAdminService(session).set_operator_role(
        principal, operator_id, role=payload.role.value, reason=payload.reason, ctx=ctx
    )
    return _operator_read(admin, 0)


@router.post(
    "/operators/{operator_id}/deactivate",
    response_model=PlatformOperatorRead,
    summary="Deactivate an operator and end their sessions (re-auth, audited)",
)
async def platform_deactivate_operator(
    operator_id: uuid.UUID,
    payload: PlatformActionReason,
    session: DbSession,
    principal: OperatorsManage,
    ctx: PlatformAudit,
) -> PlatformOperatorRead:
    service = PlatformAdminService(session)
    admin = await service.set_operator_active(
        principal, operator_id, active=False, reason=payload.reason, ctx=ctx
    )
    return _operator_read(admin, len(await service.operator_sessions(operator_id)))


@router.post(
    "/operators/{operator_id}/reactivate",
    response_model=PlatformOperatorRead,
    summary="Reactivate an operator (re-auth, audited)",
)
async def platform_reactivate_operator(
    operator_id: uuid.UUID,
    payload: PlatformActionReason,
    session: DbSession,
    principal: OperatorsManage,
    ctx: PlatformAudit,
) -> PlatformOperatorRead:
    service = PlatformAdminService(session)
    admin = await service.set_operator_active(
        principal, operator_id, active=True, reason=payload.reason, ctx=ctx
    )
    return _operator_read(admin, len(await service.operator_sessions(operator_id)))


@router.post(
    "/operators/{operator_id}/sessions/revoke",
    response_model=PlatformSessionsRevoked,
    summary="End all of an operator's sessions (re-auth, audited)",
)
async def platform_revoke_operator_sessions(
    operator_id: uuid.UUID,
    payload: PlatformActionReason,
    session: DbSession,
    principal: OperatorsManage,
    ctx: PlatformAudit,
) -> PlatformSessionsRevoked:
    ended = await PlatformAdminService(session).revoke_operator_sessions(
        principal, operator_id, reason=payload.reason, ctx=ctx
    )
    return PlatformSessionsRevoked(sessions_ended=ended)


# --- Workspaces (E5b) -----------------------------------------------------------


@router.get("/tenants", response_model=Page[PlatformTenantRead], summary="All workspaces")
async def platform_tenants(
    session: DbSession,
    _principal: TenantsRead,
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


@router.post(
    "/tenants/{tenant_id}/suspend",
    response_model=PlatformTenantRead,
    summary="Suspend a workspace (re-auth, audited)",
)
async def platform_suspend_tenant(
    tenant_id: uuid.UUID,
    payload: PlatformTenantStateChange,
    session: DbSession,
    principal: TenantsSuspend,
    ctx: PlatformAudit,
) -> PlatformTenantRead:
    row = await PlatformAdminService(session).set_tenant_active(
        tenant_id, active=False, reason=payload.reason, principal=principal, ctx=ctx
    )
    return PlatformTenantRead.model_validate(row, from_attributes=True)


@router.post(
    "/tenants/{tenant_id}/reactivate",
    response_model=PlatformTenantRead,
    summary="Reactivate a workspace (re-auth, audited)",
)
async def platform_reactivate_tenant(
    tenant_id: uuid.UUID,
    payload: PlatformTenantStateChange,
    session: DbSession,
    principal: TenantsSuspend,
    ctx: PlatformAudit,
) -> PlatformTenantRead:
    row = await PlatformAdminService(session).set_tenant_active(
        tenant_id, active=True, reason=payload.reason, principal=principal, ctx=ctx
    )
    return PlatformTenantRead.model_validate(row, from_attributes=True)


@router.get(
    "/tenants/{tenant_id}/health",
    response_model=PlatformTenantHealthRead,
    summary="A workspace's failure counts over the last 24 hours",
)
async def platform_tenant_health(
    tenant_id: uuid.UUID, session: DbSession, _principal: TenantsRead
) -> PlatformTenantHealthRead:
    health = await PlatformAdminService(session).tenant_health(tenant_id)
    return PlatformTenantHealthRead.model_validate(health, from_attributes=True)


# --- Audit ----------------------------------------------------------------------


@router.get("/audit", response_model=list[PlatformAuditRead], summary="Recent operator actions")
async def platform_audit(
    session: DbSession,
    _principal: AuditRead,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[PlatformAuditRead]:
    rows = await PlatformAdminService(session).recent_audit(limit=limit)
    return [PlatformAuditRead.model_validate(r, from_attributes=True) for r in rows]


# --- Audit and security centre (phase 9) ------------------------------------------

AuditExport = Annotated[
    PlatformPrincipal, Depends(require_platform_reauth(PlatformPermission.AUDIT_EXPORT))
]
AuditOutcome = Literal["success", "failure"]
#: The most rows one audit export returns.
AUDIT_EXPORT_LIMIT = 10_000


def _audit_entry(row: Any, email: str | None) -> PlatformAuditEntryRead:
    return PlatformAuditEntryRead(
        id=row.id,
        created_at=row.created_at,
        admin_id=row.admin_id,
        admin_email=email,
        actor_role=row.actor_role,
        action=row.action,
        outcome=row.outcome,
        target_tenant_id=row.target_tenant_id,
        target_type=row.target_type,
        target_id=row.target_id,
        detail=row.detail,
        client_ip=row.client_ip,
        user_agent=row.user_agent,
        request_id=row.request_id,
    )


@router.get(
    "/audit/search",
    response_model=Page[PlatformAuditEntryRead],
    summary="The audit trail, filtered and paged",
)
async def platform_audit_search(
    session: DbSession,
    _principal: AuditRead,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    action: Annotated[str | None, Query(max_length=64)] = None,
    outcome: AuditOutcome | None = None,
    admin_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[PlatformAuditEntryRead]:
    rows, total = await PlatformAdminService(session).search_audit(
        page=page,
        size=size,
        action=action,
        outcome=outcome,
        admin_id=admin_id,
        tenant_id=tenant_id,
        since=since,
        until=until,
    )
    return Page[PlatformAuditEntryRead].build(
        items=[_audit_entry(r, e) for r, e in rows], page=page, size=size, total_items=total
    )


@router.get(
    "/security",
    response_model=SecuritySummaryRead,
    summary="Refused sign-ins, re-authentications and permissions, and where from",
)
async def platform_security(
    session: DbSession,
    _principal: AuditRead,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
) -> SecuritySummaryRead:
    summary = await PlatformAdminService(session).security_summary(hours=hours)
    return SecuritySummaryRead(window_hours=hours, **summary)


@router.get(
    "/audit/export",
    summary="Download the filtered audit trail as CSV (re-auth, audited)",
    response_class=Response,
)
async def platform_audit_export(
    session: DbSession,
    principal: AuditExport,
    ctx: PlatformAudit,
    action: Annotated[str | None, Query(max_length=64)] = None,
    outcome: AuditOutcome | None = None,
    admin_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    service = PlatformAdminService(session)
    filters = {
        "action": action,
        "outcome": outcome,
        "admin_id": admin_id,
        "tenant_id": tenant_id,
        "since": since,
        "until": until,
    }
    entries: list[PlatformAuditEntryRead] = []
    page = 1
    while len(entries) < AUDIT_EXPORT_LIMIT:
        rows, total = await service.search_audit(page=page, size=100, **filters)
        entries.extend(_audit_entry(r, e) for r, e in rows)
        if not rows or page * 100 >= total:
            break
        page += 1
    entries = entries[:AUDIT_EXPORT_LIMIT]
    await service.record_audit_export(
        principal,
        rows=len(entries),
        filters={k: str(v) for k, v in filters.items() if v is not None},
        ctx=ctx,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "created_at",
            "admin_email",
            "actor_role",
            "action",
            "outcome",
            "target_tenant_id",
            "target_type",
            "target_id",
            "client_ip",
            "request_id",
            "detail",
        ]
    )
    for e in entries:
        writer.writerow(
            [
                _cell(v)
                for v in (
                    e.created_at.isoformat(),
                    e.admin_email,
                    e.actor_role,
                    e.action,
                    e.outcome,
                    e.target_tenant_id,
                    e.target_type,
                    e.target_id,
                    e.client_ip,
                    e.request_id,
                    json.dumps(e.detail, sort_keys=True),
                )
            ]
        )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="platform-audit-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )


#: A plain number ("-5.00") is data, not a formula; it keeps its sign.
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _cell(value: Any) -> Any:
    """Formula-looking text is prefixed with ' (the audit holds reasons
    operators typed)."""
    if (
        isinstance(value, str)
        and value[:1] in ("=", "+", "-", "@", "\t", "\r")
        and not _NUMBER.fullmatch(value)
    ):
        return "'" + value
    return value


# Console data views (D-019), after the identity routes.
router.include_router(console_router)
router.include_router(workspace_data_router)
