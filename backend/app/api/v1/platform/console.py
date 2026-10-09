"""Operator console data views (Admin Control Center, D-019).

Mounted under the platform router, so every route here is already behind
the network allow-list and a platform session. Each route also names its
permission; workspace routes enter the workspace through
:func:`app.api.deps.platform_workspace`, which audits the visit and sets the
tenant context for the tenant-scoped repositories.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    DbSession,
    PlatformAudit,
    platform_workspace,
    require_platform_permission,
    require_platform_reauth,
)
from app.core.platform_permissions import PlatformPermission
from app.core.redis import check_redis_health
from app.repositories.billing import FeatureFlagRepository
from app.repositories.platform_jobs import JobKind, JobState, PlatformJobsMonitor
from app.repositories.platform_metrics import (
    PlatformMetrics,
    database_answers,
    migration_revision,
)
from app.schemas.common import Page
from app.schemas.platform_console import (
    DailyCountRead,
    GlobalFlagChange,
    GlobalFlagRead,
    PlatformDashboardRead,
    PlatformJobRead,
    SubscriptionSummaryRead,
    SystemHealthRead,
    WorkspaceHealthRead,
    WorkspaceOverviewRead,
)
from app.services.platform_admin import PlatformAdminService, PlatformPrincipal
from app.services.platform_workspace import PlatformWorkspace, PlatformWorkspaceService

router = APIRouter()

DashboardRead = Annotated[
    PlatformPrincipal, Depends(require_platform_permission(PlatformPermission.DASHBOARD_READ))
]
# scope="function", like DbSession, which it depends on: the tenant context
# is cleared as soon as the handler returns, before the response is sent.
Workspace = Annotated[PlatformWorkspace, Depends(platform_workspace(), scope="function")]


@router.get(
    "/dashboard", response_model=PlatformDashboardRead, summary="Platform-wide metrics and health"
)
async def platform_dashboard(
    session: DbSession, _principal: DashboardRead
) -> PlatformDashboardRead:
    snap = await PlatformMetrics(session).snapshot()
    return PlatformDashboardRead(
        generated_at=snap.generated_at,
        tenants_by_status=snap.tenants_by_status,
        tenants_new_week=snap.tenants_new_week,
        tenants_new_month=snap.tenants_new_month,
        users_active=snap.users_active,
        users_new_week=snap.users_new_week,
        stores_by_status=snap.stores_by_status,
        stores_by_platform=snap.stores_by_platform,
        products_total=snap.products_total,
        listings_by_status=snap.listings_by_status,
        orders_last_day=snap.orders_last_day,
        orders_last_week=snap.orders_last_week,
        subscriptions_by_plan=snap.subscriptions_by_plan,
        subscriptions_by_status=snap.subscriptions_by_status,
        trials_ending_week=snap.trials_ending_week,
        failed_last_day=snap.failed_last_day,
        stuck=snap.stuck,
        operator_sessions_open=snap.operator_sessions_open,
        security_failures_last_day=snap.security_failures_last_day,
        signups_by_day=[DailyCountRead(day=d.day, count=d.count) for d in snap.signups_by_day],
        orders_by_day=[DailyCountRead(day=d.day, count=d.count) for d in snap.orders_by_day],
        system=SystemHealthRead(
            database=await database_answers(session),
            redis=await check_redis_health(),
            migration_revision=await migration_revision(session),
        ),
    )


@router.get(
    "/workspaces/{tenant_id}",
    response_model=WorkspaceOverviewRead,
    summary="One workspace: counts, subscription and health (audited)",
)
async def platform_workspace_overview(
    workspace: Workspace, session: DbSession
) -> WorkspaceOverviewRead:
    view = await PlatformWorkspaceService(session).overview(workspace.tenant)
    tenant, sub, health = view.tenant, view.subscription, view.health
    return WorkspaceOverviewRead(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status.value,
        is_active=tenant.is_active,
        timezone=tenant.timezone,
        default_currency=tenant.default_currency,
        created_at=tenant.created_at,
        users=view.users,
        active_users=view.active_users,
        stores_by_status=view.stores_by_status,
        products=view.products,
        orders_by_status=view.orders_by_status,
        listings_by_status=view.listings_by_status,
        subscription=None
        if sub is None
        else SubscriptionSummaryRead(
            plan=sub.plan,
            status=sub.status,
            ai_addon=sub.ai_addon,
            trial_ends_at=sub.trial_ends_at,
            current_period_end=sub.current_period_end,
            cancel_at_period_end=sub.cancel_at_period_end,
            has_stripe_customer=sub.has_stripe_customer,
        ),
        health=WorkspaceHealthRead(
            window_hours=health.window_hours,
            failed_order_syncs=health.failed_order_syncs,
            failed_inventory_syncs=health.failed_inventory_syncs,
            listings_in_error=health.listings_in_error,
            failed_notification_emails=health.failed_notification_emails,
        ),
    )


# --- Jobs across workspaces (phase 7) ------------------------------------------------

JobsRead = Annotated[
    PlatformPrincipal, Depends(require_platform_permission(PlatformPermission.JOBS_READ))
]


@router.get(
    "/jobs/summary",
    response_model=dict[str, dict[str, int]],
    summary="Failed (7 days) and stuck jobs per kind, across workspaces",
)
async def platform_jobs_summary(
    session: DbSession, _principal: JobsRead
) -> dict[str, dict[str, int]]:
    return await PlatformJobsMonitor(session).summary()


@router.get(
    "/jobs",
    response_model=Page[PlatformJobRead],
    summary="Failed or stuck jobs of one kind, across workspaces",
)
async def platform_jobs(
    session: DbSession,
    _principal: JobsRead,
    kind: JobKind = "order_sync",
    state: JobState = "failed",
    # camelCase like every other API field; without the alias FastAPI reads
    # "tenant_id" and silently ignores the filter the console sends.
    tenant_id: Annotated[uuid.UUID | None, Query(alias="tenantId")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[PlatformJobRead]:
    rows, total = await PlatformJobsMonitor(session).page(
        kind=kind, state=state, page=page, size=size, tenant_id=tenant_id
    )
    return Page[PlatformJobRead].build(
        items=[PlatformJobRead.model_validate(r, from_attributes=True) for r in rows],
        page=page,
        size=size,
        total_items=total,
    )


# --- Feature switch defaults (phase 8) --------------------------------------------------

SettingsWrite = Annotated[
    PlatformPrincipal, Depends(require_platform_reauth(PlatformPermission.SETTINGS_MANAGE))
]
BillingReader = Annotated[
    PlatformPrincipal, Depends(require_platform_permission(PlatformPermission.BILLING_READ))
]


@router.get(
    "/feature-flags",
    response_model=list[GlobalFlagRead],
    summary="Platform-wide defaults of the feature switches",
)
async def platform_feature_flags(
    session: DbSession, _principal: BillingReader
) -> list[GlobalFlagRead]:
    rows = await FeatureFlagRepository(session).all_flags()
    return [GlobalFlagRead.model_validate(r, from_attributes=True) for r in rows]


@router.post(
    "/feature-flags/{key}",
    response_model=list[GlobalFlagRead],
    summary="Change a switch's platform-wide default (super admin, re-auth, audited)",
)
async def platform_set_feature_flag(
    key: str,
    payload: GlobalFlagChange,
    session: DbSession,
    principal: SettingsWrite,
    ctx: PlatformAudit,
) -> list[GlobalFlagRead]:
    await PlatformAdminService(session).set_global_flag(
        principal, key, enabled=payload.enabled, reason=payload.reason, ctx=ctx
    )
    rows = await FeatureFlagRepository(session).all_flags()
    return [GlobalFlagRead.model_validate(r, from_attributes=True) for r in rows]
