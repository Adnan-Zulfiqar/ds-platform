"""Operator console data views (Admin Control Center, D-019).

Mounted under the platform router, so every route here is already behind
the network allow-list and a platform session. Each route also names its
permission; workspace routes enter the workspace through
:func:`app.api.deps.platform_workspace`, which audits the visit and sets the
tenant context for the tenant-scoped repositories.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, platform_workspace, require_platform_permission
from app.core.platform_permissions import PlatformPermission
from app.core.redis import check_redis_health
from app.repositories.platform_metrics import (
    PlatformMetrics,
    database_answers,
    migration_revision,
)
from app.schemas.platform_console import (
    DailyCountRead,
    PlatformDashboardRead,
    SubscriptionSummaryRead,
    SystemHealthRead,
    WorkspaceHealthRead,
    WorkspaceOverviewRead,
)
from app.services.platform_admin import PlatformPrincipal
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
        tenants_new_7d=snap.tenants_new_7d,
        tenants_new_30d=snap.tenants_new_30d,
        users_active=snap.users_active,
        users_new_7d=snap.users_new_7d,
        stores_by_status=snap.stores_by_status,
        stores_by_platform=snap.stores_by_platform,
        products_total=snap.products_total,
        listings_by_status=snap.listings_by_status,
        orders_24h=snap.orders_24h,
        orders_7d=snap.orders_7d,
        subscriptions_by_plan=snap.subscriptions_by_plan,
        subscriptions_by_status=snap.subscriptions_by_status,
        trials_ending_7d=snap.trials_ending_7d,
        failed_24h=snap.failed_24h,
        stuck=snap.stuck,
        operator_sessions_open=snap.operator_sessions_open,
        security_failures_24h=snap.security_failures_24h,
        signups_30d=[DailyCountRead(day=d.day, count=d.count) for d in snap.signups_30d],
        orders_14d=[DailyCountRead(day=d.day, count=d.count) for d in snap.orders_14d],
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
