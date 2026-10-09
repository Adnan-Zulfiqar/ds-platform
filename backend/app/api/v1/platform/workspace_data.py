"""Inside one workspace: the drill-down views (Admin Control Center phase 3, D-019).

Every route enters the workspace through :func:`platform_workspace`, so the
operator's permission is checked, the visit is audited and the
tenant-scoped repositories are used. Exports also need a recent
re-authentication and write their own audit row with the row count.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response

from app.api.deps import DbSession, PlatformAudit, platform_workspace
from app.core.platform_permissions import PlatformPermission
from app.repositories.role import RoleRepository
from app.schemas.common import ListQueryParams, Page, SortDirection, list_query_params
from app.schemas.platform_console import (
    InventorySyncNow,
    JobActionResult,
    ListingsResyncQueued,
    SupportSessionOpen,
    SupportSessionRead,
    WebhookReconcileResult,
    WorkspaceChangeReason,
    WorkspaceConnectionRead,
    WorkspaceImportRead,
    WorkspaceInvitationRead,
    WorkspaceListingRead,
    WorkspaceNotificationRead,
    WorkspaceOrderDetailRead,
    WorkspaceOrderEventRead,
    WorkspaceOrderItemRead,
    WorkspaceOrderRead,
    WorkspaceProductDetailRead,
    WorkspaceProductRead,
    WorkspaceRoleChange,
    WorkspaceShipmentRead,
    WorkspaceStoreRead,
    WorkspaceSupplierOrderRead,
    WorkspaceSyncRunRead,
    WorkspaceUserChanged,
    WorkspaceUserRead,
    WorkspaceVariantRead,
)
from app.services.platform_admin import PlatformAdminService
from app.services.platform_workspace import (
    PlatformCatalogActions,
    PlatformJobActions,
    PlatformStoreActions,
    PlatformWorkspace,
    PlatformWorkspaceActions,
    PlatformWorkspaceService,
    WorkspaceUserView,
)
from app.tasks.integrations.channels import push_price_quantity_after_commit

router = APIRouter(prefix="/workspaces/{tenant_id}")

Workspace = Annotated[PlatformWorkspace, Depends(platform_workspace(), scope="function")]
WorkspaceExport = Annotated[
    PlatformWorkspace,
    Depends(
        platform_workspace(PlatformPermission.WORKSPACE_DATA_READ, reauth=True), scope="function"
    ),
]
Params = Annotated[ListQueryParams, Depends(list_query_params)]

#: The most rows one export returns. Larger needs are a database job, not
#: a browser download.
EXPORT_LIMIT = 5000


def _user_read(view: WorkspaceUserView) -> WorkspaceUserRead:
    u = view.user
    return WorkspaceUserRead(
        id=u.id,
        email=u.email,
        first_name=u.first_name,
        last_name=u.last_name,
        is_active=u.is_active,
        is_verified=u.is_verified,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
        roles=view.roles,
        active_sessions=view.active_sessions,
    )


def _product_read(product: Any, variant_count: int) -> WorkspaceProductRead:
    return WorkspaceProductRead(
        id=product.id,
        title=product.title,
        status=product.status,
        source=product.source,
        external_id=product.external_id,
        supplier_name=product.supplier_name,
        currency=product.currency,
        cost_price_min=product.cost_price_min,
        sell_price=product.sell_price,
        stock_quantity=product.stock_quantity,
        ai_status=product.ai_status,
        needs_review=product.needs_review,
        variant_count=variant_count,
        last_synced_at=product.last_synced_at,
        last_sync_error=product.last_sync_error,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _run_read(kind: str, run: Any) -> WorkspaceSyncRunRead:
    return WorkspaceSyncRunRead(
        kind=kind,
        id=run.id,
        status=run.status,
        trigger=run.trigger,
        store_id=getattr(run, "store_id", None),
        error_code=run.error_code,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )


# --- People ---------------------------------------------------------------------


@router.get("/users", response_model=Page[WorkspaceUserRead], summary="Workspace users")
async def workspace_users(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    active: bool | None = None,
) -> Page[WorkspaceUserRead]:
    views, total = await PlatformWorkspaceService(session).users(params, active=active)
    return Page[WorkspaceUserRead].build(
        items=[_user_read(v) for v in views], page=params.page, size=params.size, total_items=total
    )


@router.get(
    "/invitations", response_model=Page[WorkspaceInvitationRead], summary="Team invitations"
)
async def workspace_invitations(
    workspace: Workspace, session: DbSession, params: Params
) -> Page[WorkspaceInvitationRead]:
    rows, total = await PlatformWorkspaceService(session).invitations(params)
    return Page[WorkspaceInvitationRead].build(
        items=[WorkspaceInvitationRead.model_validate(r, from_attributes=True) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


# --- Stores and integrations ------------------------------------------------------


@router.get("/stores", response_model=Page[WorkspaceStoreRead], summary="Connected stores")
async def workspace_stores(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    platform: Annotated[str | None, Query(max_length=32)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
) -> Page[WorkspaceStoreRead]:
    rows, total = await PlatformWorkspaceService(session).stores(
        params, platform=platform, status=status
    )
    return Page[WorkspaceStoreRead].build(
        items=[WorkspaceStoreRead.model_validate(r, from_attributes=True) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/connections",
    response_model=list[WorkspaceConnectionRead],
    summary="Supplier and channel connections (no credentials)",
)
async def workspace_connections(
    workspace: Workspace, session: DbSession
) -> list[WorkspaceConnectionRead]:
    rows = await PlatformWorkspaceService(session).connections()
    return [WorkspaceConnectionRead.model_validate(r, from_attributes=True) for r in rows]


# --- Catalogue ------------------------------------------------------------------


@router.get("/products", response_model=Page[WorkspaceProductRead], summary="Products or drafts")
async def workspace_products(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    publication: Literal["draft", "published"] = "published",
) -> Page[WorkspaceProductRead]:
    rows, total = await PlatformWorkspaceService(session).products(params, publication=publication)
    return Page[WorkspaceProductRead].build(
        items=[_product_read(p, n) for p, n in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/products/{product_id}",
    response_model=WorkspaceProductDetailRead,
    summary="One product with variants, images and listings",
)
async def workspace_product(
    product_id: uuid.UUID, workspace: Workspace, session: DbSession
) -> WorkspaceProductDetailRead:
    view = await PlatformWorkspaceService(session).product(product_id)
    base = _product_read(view.product, len(view.variants))
    return WorkspaceProductDetailRead(
        **base.model_dump(),
        description=view.product.description,
        external_url=view.product.external_url,
        category_name=view.product.category_name,
        tags=list(view.product.tags or []),
        images=view.images,
        variants=[
            WorkspaceVariantRead.model_validate(v, from_attributes=True) for v in view.variants
        ],
        listings=[
            WorkspaceListingRead.model_validate(li, from_attributes=True) for li in view.listings
        ],
    )


@router.get("/listings", response_model=Page[WorkspaceListingRead], summary="Channel listings")
async def workspace_listings(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    status: Annotated[str | None, Query(max_length=16)] = None,
    store_id: uuid.UUID | None = None,
) -> Page[WorkspaceListingRead]:
    rows, total = await PlatformWorkspaceService(session).listings(
        params, status=status, store_id=store_id
    )
    return Page[WorkspaceListingRead].build(
        items=[WorkspaceListingRead.model_validate(r, from_attributes=True) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


# --- Orders -----------------------------------------------------------------------


def _order_read(order: Any) -> WorkspaceOrderRead:
    return WorkspaceOrderRead.model_validate(order, from_attributes=True)


@router.get("/orders", response_model=Page[WorkspaceOrderRead], summary="Orders")
async def workspace_orders(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    fulfillment_status: Annotated[str | None, Query(max_length=32)] = None,
    source: Annotated[str | None, Query(max_length=32)] = None,
) -> Page[WorkspaceOrderRead]:
    rows, total = await PlatformWorkspaceService(session).orders(
        params, fulfillment_status=fulfillment_status, source=source
    )
    return Page[WorkspaceOrderRead].build(
        items=[_order_read(r) for r in rows], page=params.page, size=params.size, total_items=total
    )


@router.get(
    "/orders/{order_id}",
    response_model=WorkspaceOrderDetailRead,
    summary="One order with items, shipments, events and supplier order",
)
async def workspace_order(
    order_id: uuid.UUID, workspace: Workspace, session: DbSession
) -> WorkspaceOrderDetailRead:
    view = await PlatformWorkspaceService(session).order(order_id)
    o = view.order
    return WorkspaceOrderDetailRead(
        **_order_read(o).model_dump(),
        recipient_name=o.recipient_name,
        recipient_phone=o.recipient_phone,
        address_line1=o.address_line1,
        address_line2=o.address_line2,
        city=o.city,
        province=o.province,
        postal_code=o.postal_code,
        country_code=o.country_code,
        shipping_amount=o.shipping_amount,
        paid_at=o.paid_at,
        delivered_at=o.delivered_at,
        items=[WorkspaceOrderItemRead.model_validate(i, from_attributes=True) for i in view.items],
        shipments=[
            WorkspaceShipmentRead.model_validate(s, from_attributes=True) for s in view.shipments
        ],
        events=[
            WorkspaceOrderEventRead.model_validate(e, from_attributes=True) for e in view.events
        ],
        supplier_orders=[
            WorkspaceSupplierOrderRead.model_validate(s, from_attributes=True)
            for s in view.supplier_orders
        ],
    )


# --- Background work and notifications ------------------------------------------


@router.get(
    "/sync-runs", response_model=Page[WorkspaceSyncRunRead], summary="Order or inventory sync runs"
)
async def workspace_sync_runs(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    kind: Literal["orders", "inventory"] = "orders",
    status: Annotated[str | None, Query(max_length=16)] = None,
) -> Page[WorkspaceSyncRunRead]:
    service = PlatformWorkspaceService(session)
    rows: Sequence[Any]
    if kind == "orders":
        rows, total = await service.order_sync_runs(params, status=status)
    else:
        rows, total = await service.inventory_sync_runs(params, status=status)
    return Page[WorkspaceSyncRunRead].build(
        items=[_run_read(kind, r) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/notifications", response_model=Page[WorkspaceNotificationRead], summary="Notifications"
)
async def workspace_notifications(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    kind: Annotated[str | None, Query(max_length=32)] = None,
) -> Page[WorkspaceNotificationRead]:
    rows, total = await PlatformWorkspaceService(session).notifications(params, kind=kind)
    return Page[WorkspaceNotificationRead].build(
        items=[WorkspaceNotificationRead.model_validate(r, from_attributes=True) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


# --- Export -------------------------------------------------------------------------

ExportDataset = Literal["users", "stores", "products", "drafts", "listings", "orders"]


def _cell(value: Any) -> Any:
    """One CSV cell. A text cell starting with ``=``, ``+``, ``-``, ``@`` or a
    tab is prefixed with ``'`` so a spreadsheet shows it rather than running
    it as a formula: product titles and buyer names are merchant input."""
    if isinstance(value, list):
        value = ";".join(map(str, value))
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


async def _collect(
    fetch: Callable[[ListQueryParams], Any], to_row: Callable[[Any], dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while len(rows) < EXPORT_LIMIT:
        params = ListQueryParams(
            page=page, size=100, sort_by="created_at", sort_dir=SortDirection.DESC
        )
        batch, total = await fetch(params)
        rows.extend(to_row(item) for item in batch)
        if page * params.size >= total or not batch:
            break
        page += 1
    return rows[:EXPORT_LIMIT]


@router.get(
    "/export/{dataset}",
    summary="Download one dataset as CSV (re-auth, audited, at most 5000 rows)",
    response_class=Response,
)
async def workspace_export(
    dataset: ExportDataset,
    workspace: WorkspaceExport,
    session: DbSession,
    ctx: PlatformAudit,
) -> Response:
    service = PlatformWorkspaceService(session)
    rows: list[dict[str, Any]]
    if dataset == "users":
        rows = await _collect(
            lambda p: service.users(p, active=None),
            lambda v: _user_read(v).model_dump(mode="json"),
        )
    elif dataset == "stores":
        rows = await _collect(
            lambda p: service.stores(p, platform=None, status=None),
            lambda r: WorkspaceStoreRead.model_validate(r, from_attributes=True).model_dump(
                mode="json"
            ),
        )
    elif dataset in ("products", "drafts"):
        publication: Literal["draft", "published"] = "draft" if dataset == "drafts" else "published"
        rows = await _collect(
            lambda p: service.products(p, publication=publication),
            lambda pair: _product_read(pair[0], pair[1]).model_dump(mode="json"),
        )
    elif dataset == "listings":
        rows = await _collect(
            lambda p: service.listings(p, status=None, store_id=None),
            lambda r: WorkspaceListingRead.model_validate(r, from_attributes=True).model_dump(
                mode="json"
            ),
        )
    else:
        rows = await _collect(
            lambda p: service.orders(p, fulfillment_status=None, source=None),
            lambda r: _order_read(r).model_dump(mode="json"),
        )

    await PlatformAdminService(session).record_workspace_export(
        workspace.principal, workspace.tenant.id, dataset=dataset, rows=len(rows), ctx=ctx
    )
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _cell(v) for k, v in row.items()})
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"{workspace.tenant.slug}-{dataset}-{stamp}.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# --- Support session and changes (phase 4) ----------------------------------------

SupportOpen = Annotated[
    PlatformWorkspace,
    Depends(platform_workspace(PlatformPermission.SUPPORT_SESSION, reauth=True), scope="function"),
]
UsersWrite = Annotated[
    PlatformWorkspace,
    Depends(platform_workspace(PlatformPermission.USERS_MANAGE, write=True), scope="function"),
]


def _support_read(row: Any) -> SupportSessionRead:
    return SupportSessionRead.model_validate(row, from_attributes=True)


@router.get(
    "/support-session",
    response_model=SupportSessionRead | None,
    summary="Your open support session for this workspace, if any",
)
async def workspace_support_session(
    workspace: Workspace, session: DbSession
) -> SupportSessionRead | None:
    row = await PlatformAdminService(session).active_support_session(
        workspace.principal, workspace.tenant.id
    )
    return None if row is None else _support_read(row)


@router.post(
    "/support-session",
    response_model=SupportSessionRead,
    summary="Open a support session (re-auth, reason, visible to the workspace)",
)
async def workspace_open_support_session(
    payload: SupportSessionOpen, workspace: SupportOpen, session: DbSession, ctx: PlatformAudit
) -> SupportSessionRead:
    row = await PlatformAdminService(session).open_support_session(
        workspace.principal,
        workspace.tenant.id,
        minutes=payload.minutes,
        reason=payload.reason,
        ctx=ctx,
    )
    return _support_read(row)


@router.post(
    "/support-session/end",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End your support session for this workspace",
)
async def workspace_end_support_session(
    workspace: Workspace, session: DbSession, ctx: PlatformAudit
) -> None:
    await PlatformAdminService(session).end_support_session(
        workspace.principal, workspace.tenant.id, ctx=ctx
    )


async def _user_changed(
    session: Any, user: Any, *, sessions_ended: int = 0
) -> WorkspaceUserChanged:
    roles = await RoleRepository(session).list_role_names_for_user(user.id)
    return WorkspaceUserChanged(
        id=user.id, is_active=user.is_active, roles=sorted(roles), sessions_ended=sessions_ended
    )


@router.post(
    "/users/{user_id}/disable",
    response_model=WorkspaceUserChanged,
    summary="Disable a user and end their sessions (support session, re-auth, audited)",
)
async def workspace_disable_user(
    user_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: UsersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceUserChanged:
    user = await PlatformWorkspaceActions(session, workspace, ctx).set_user_active(
        user_id, active=False, reason=payload.reason
    )
    return await _user_changed(session, user)


@router.post(
    "/users/{user_id}/enable",
    response_model=WorkspaceUserChanged,
    summary="Enable a user (support session, re-auth, audited)",
)
async def workspace_enable_user(
    user_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: UsersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceUserChanged:
    user = await PlatformWorkspaceActions(session, workspace, ctx).set_user_active(
        user_id, active=True, reason=payload.reason
    )
    return await _user_changed(session, user)


@router.post(
    "/users/{user_id}/end-sessions",
    response_model=WorkspaceUserChanged,
    summary="Sign a user out everywhere (support session, re-auth, audited)",
)
async def workspace_end_user_sessions(
    user_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: UsersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceUserChanged:
    actions = PlatformWorkspaceActions(session, workspace, ctx)
    ended = await actions.end_user_sessions(user_id, reason=payload.reason)
    return await _user_changed(session, await actions.user(user_id), sessions_ended=ended)


@router.post(
    "/users/{user_id}/require-password-reset",
    response_model=WorkspaceUserChanged,
    summary="Stop the current password working (support session, re-auth, audited)",
)
async def workspace_require_password_reset(
    user_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: UsersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceUserChanged:
    actions = PlatformWorkspaceActions(session, workspace, ctx)
    await actions.require_password_reset(user_id, reason=payload.reason)
    return await _user_changed(session, await actions.user(user_id))


@router.post(
    "/users/{user_id}/role",
    response_model=WorkspaceUserChanged,
    summary="Change a member's workspace role, never an owner's (support session, re-auth)",
)
async def workspace_set_user_role(
    user_id: uuid.UUID,
    payload: WorkspaceRoleChange,
    workspace: UsersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceUserChanged:
    actions = PlatformWorkspaceActions(session, workspace, ctx)
    await actions.set_member_role(user_id, role=payload.role, reason=payload.reason)
    return await _user_changed(session, await actions.user(user_id))


@router.post(
    "/invitations/{invitation_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an open invitation (support session, re-auth, audited)",
)
async def workspace_revoke_invitation(
    invitation_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: UsersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> None:
    await PlatformWorkspaceActions(session, workspace, ctx).revoke_invitation(
        invitation_id, reason=payload.reason
    )


# --- Stores and integrations (phase 5) -------------------------------------------

StoresWrite = Annotated[
    PlatformWorkspace,
    Depends(platform_workspace(PlatformPermission.STORES_MANAGE, write=True), scope="function"),
]


@router.post(
    "/stores/{store_id}/pause",
    response_model=WorkspaceStoreRead,
    summary="Stop DropPilot writing to a store (support session, re-auth, audited)",
)
async def workspace_pause_store(
    store_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: StoresWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceStoreRead:
    store = await PlatformStoreActions(session, workspace, ctx).set_store_paused(
        store_id, paused=True, reason=payload.reason
    )
    return WorkspaceStoreRead.model_validate(store, from_attributes=True)


@router.post(
    "/stores/{store_id}/resume",
    response_model=WorkspaceStoreRead,
    summary="Let DropPilot write to a store again (support session, re-auth, audited)",
)
async def workspace_resume_store(
    store_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: StoresWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceStoreRead:
    store = await PlatformStoreActions(session, workspace, ctx).set_store_paused(
        store_id, paused=False, reason=payload.reason
    )
    return WorkspaceStoreRead.model_validate(store, from_attributes=True)


@router.post(
    "/sync/orders",
    response_model=WorkspaceSyncRunRead,
    summary="Run the supplier order sync now (support session, re-auth, audited)",
)
async def workspace_sync_orders_now(
    payload: WorkspaceChangeReason, workspace: StoresWrite, session: DbSession, ctx: PlatformAudit
) -> WorkspaceSyncRunRead:
    run = await PlatformStoreActions(session, workspace, ctx).sync_orders_now(reason=payload.reason)
    return _run_read("orders", run)


@router.post(
    "/sync/inventory",
    response_model=WorkspaceSyncRunRead,
    summary="Run the inventory sync now, for one store or all (support session, re-auth)",
)
async def workspace_sync_inventory_now(
    payload: InventorySyncNow, workspace: StoresWrite, session: DbSession, ctx: PlatformAudit
) -> WorkspaceSyncRunRead:
    run, changed = await PlatformStoreActions(session, workspace, ctx).sync_inventory_now(
        store_id=payload.store_id, reason=payload.reason
    )
    # As for the merchant's own sync: changed stock reaches the channels once
    # this request has committed (and never to a paused store).
    push_price_quantity_after_commit(session, changed)
    return _run_read("inventory", run)


@router.post(
    "/stores/{store_id}/shopify/webhooks",
    response_model=WebhookReconcileResult,
    summary="Re-register a Shopify store's webhooks (support session, re-auth, audited)",
)
async def workspace_register_shopify_webhooks(
    store_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: StoresWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WebhookReconcileResult:
    healthy = await PlatformStoreActions(session, workspace, ctx).register_shopify_webhooks(
        store_id, reason=payload.reason
    )
    return WebhookReconcileResult(store_id=store_id, healthy=healthy)


# --- Catalogue and orders (phase 6) -------------------------------------------------

CatalogWrite = Annotated[
    PlatformWorkspace,
    Depends(platform_workspace(PlatformPermission.CATALOG_MANAGE, write=True), scope="function"),
]
OrdersWrite = Annotated[
    PlatformWorkspace,
    Depends(platform_workspace(PlatformPermission.ORDERS_MANAGE, write=True), scope="function"),
]


@router.get("/imports", response_model=Page[WorkspaceImportRead], summary="Product import attempts")
async def workspace_imports(
    workspace: Workspace,
    session: DbSession,
    params: Params,
    status: Annotated[str | None, Query(max_length=16)] = None,
) -> Page[WorkspaceImportRead]:
    rows, total = await PlatformWorkspaceService(session).imports(params, status=status)
    return Page[WorkspaceImportRead].build(
        items=[WorkspaceImportRead.model_validate(r, from_attributes=True) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.post(
    "/imports/{import_id}/retry",
    response_model=WorkspaceProductRead,
    summary="Retry a failed product import (support session, re-auth, audited)",
)
async def workspace_retry_import(
    import_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: CatalogWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceProductRead:
    product = await PlatformCatalogActions(session, workspace, ctx).retry_import(
        import_id, reason=payload.reason
    )
    return _product_read(product, 0)


@router.post(
    "/products/{product_id}/resync-listings",
    response_model=ListingsResyncQueued,
    summary="Push one product's price and stock to its channels again (audited)",
)
async def workspace_resync_listings(
    product_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: CatalogWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> ListingsResyncQueued:
    count = await PlatformCatalogActions(session, workspace, ctx).resync_listings(
        product_id, reason=payload.reason
    )
    push_price_quantity_after_commit(session, [product_id])
    return ListingsResyncQueued(product_id=product_id, listings=count)


@router.post(
    "/orders/{order_id}/refresh",
    response_model=WorkspaceOrderRead,
    summary="Re-read one order from the supplier (support session, re-auth, audited)",
)
async def workspace_refresh_order(
    order_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: OrdersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceOrderRead:
    order = await PlatformCatalogActions(session, workspace, ctx).refresh_order(
        order_id, reason=payload.reason
    )
    return _order_read(order)


@router.post(
    "/orders/{order_id}/supplier-order/release",
    response_model=WorkspaceSupplierOrderRead,
    summary="Release a supplier order stuck while being sent (support session, re-auth)",
)
async def workspace_release_supplier_order(
    order_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: OrdersWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> WorkspaceSupplierOrderRead:
    row = await PlatformCatalogActions(session, workspace, ctx).release_supplier_order(
        order_id, reason=payload.reason
    )
    return WorkspaceSupplierOrderRead.model_validate(row, from_attributes=True)


# --- Job actions (phase 7) ------------------------------------------------------------

JobsWrite = Annotated[
    PlatformWorkspace,
    Depends(platform_workspace(PlatformPermission.JOBS_MANAGE, write=True), scope="function"),
]


@router.post(
    "/jobs/{kind}/{run_id}/close",
    response_model=JobActionResult,
    summary="Close a sync run stuck in running (support session, re-auth, audited)",
)
async def workspace_close_stuck_sync(
    kind: Literal["order_sync", "inventory_sync"],
    run_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: JobsWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> JobActionResult:
    run = await PlatformJobActions(session, workspace, ctx).close_stuck_sync(
        kind, run_id, reason=payload.reason
    )
    return JobActionResult(kind=kind, id=run.id, outcome=str(run.status))


@router.post(
    "/jobs/pipeline_run/{run_id}/cancel",
    response_model=JobActionResult,
    summary="Cancel an AI pipeline run (support session, re-auth, audited)",
)
async def workspace_cancel_pipeline_run(
    run_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: JobsWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> JobActionResult:
    outcome = await PlatformJobActions(session, workspace, ctx).cancel_pipeline_run(
        run_id, reason=payload.reason
    )
    return JobActionResult(kind="pipeline_run", id=run_id, outcome=outcome)


@router.post(
    "/jobs/rule_application/{application_id}/cancel",
    response_model=JobActionResult,
    summary="Cancel a pricing rule application (support session, re-auth, audited)",
)
async def workspace_cancel_rule_application(
    application_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: JobsWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> JobActionResult:
    outcome = await PlatformJobActions(session, workspace, ctx).cancel_rule_application(
        application_id, reason=payload.reason
    )
    return JobActionResult(kind="rule_application", id=application_id, outcome=outcome)


@router.post(
    "/jobs/automation_run/{run_id}/retry",
    response_model=JobActionResult,
    summary="Run a failed automation's rule again (support session, re-auth, audited)",
)
async def workspace_retry_automation(
    run_id: uuid.UUID,
    payload: WorkspaceChangeReason,
    workspace: JobsWrite,
    session: DbSession,
    ctx: PlatformAudit,
) -> JobActionResult:
    run = await PlatformJobActions(session, workspace, ctx).retry_automation(
        run_id, reason=payload.reason
    )
    return JobActionResult(kind="automation_run", id=run.id, outcome=str(run.status))
