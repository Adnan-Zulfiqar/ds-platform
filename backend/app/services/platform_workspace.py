"""One workspace, seen by a platform operator (Admin Control Center, D-019).

Every method here runs **inside the workspace's tenant context**, set by
``app.api.deps.platform_workspace`` after the operator's permission is
checked and the visit is audited. That is the whole design: the operator
reads through the same ``TenantScopedRepository`` classes the merchant's own
requests use, so the tenant predicate is applied exactly as it is for them,
and one operator request can never span two workspaces. No unscoped query
is added for workspace data.

Secrets never leave: responses are built from explicit schemas that do not
name encrypted columns, password hashes or token hashes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.integrations.shopify.service import ShopifyService
from app.models.automation import AutomationRun
from app.models.inventory import InventorySyncRun
from app.models.invitation import UserInvitation
from app.models.notification import Notification, NotificationKind
from app.models.order import (
    FulfillmentStatus,
    Order,
    OrderEvent,
    OrderItem,
    OrderSource,
    OrderSyncRun,
    Shipment,
    SyncRunStatus,
    SyncTrigger,
)
from app.models.product import ImportStatus, Product, ProductImport, ProductVariant
from app.models.role import RoleName
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.supplier_order import SupplierOrder
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.automation import AutomationRunRepository
from app.repositories.billing import TenantSubscriptionRepository
from app.repositories.ebay import EbayConnectionRepository
from app.repositories.integration import AliExpressConnectionRepository
from app.repositories.inventory import InventorySyncRunRepository
from app.repositories.invitation import UserInvitationRepository
from app.repositories.notification import NotificationRepository
from app.repositories.order import (
    OrderEventRepository,
    OrderItemRepository,
    OrderRepository,
    OrderSyncRunRepository,
    ShipmentRepository,
)
from app.repositories.platform_admin import PlatformTenantDirectory, TenantHealth
from app.repositories.platform_jobs import RUN_STUCK_AFTER
from app.repositories.product import (
    ProductImageRepository,
    ProductImportRepository,
    ProductRepository,
    ProductVariantRepository,
)
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.role import RoleRepository
from app.repositories.shopify import ShopifyConnectionRepository, StoreListingRepository
from app.repositories.store import StoreRepository
from app.repositories.supplier_order import SupplierOrderRepository
from app.repositories.user import UserRepository
from app.schemas.common import ListQueryParams, SortDirection
from app.services.automation_service import AutomationService
from app.services.base import BaseService
from app.services.inventory_sync import InventorySyncService
from app.services.order_sync import OrderSyncService
from app.services.pipeline_bulk import PipelineBulkRunService
from app.services.platform_admin import AuditContext, PlatformAdminService, PlatformPrincipal
from app.services.product_import import ProductImportService
from app.services.rule_application import RuleApplicationService
from app.services.supplier_ordering import SupplierOrderingService
from app.services.team_invitations import TeamInvitationService


def _parse[E: StrEnum](enum: type[E], value: str) -> E:
    """A filter value from the query string; an unknown one is a 422, not a
    500."""
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum)
        raise ValidationError(f"Unknown value {value!r}; expected one of: {allowed}.") from None


@dataclass(frozen=True, slots=True)
class SubscriptionSummary:
    plan: str | None
    status: str
    ai_addon: bool
    trial_ends_at: datetime
    current_period_end: datetime | None
    cancel_at_period_end: bool
    #: Whether a Stripe customer exists. The id itself stays in Stripe's
    #: dashboard; the console has no use for it.
    has_stripe_customer: bool


@dataclass(frozen=True, slots=True)
class WorkspaceOverview:
    tenant: Tenant
    users: int
    active_users: int
    stores_by_status: dict[str, int]
    products: dict[str, int]
    orders_by_status: dict[str, int]
    listings_by_status: dict[str, int]
    subscription: SubscriptionSummary | None
    health: TenantHealth


@dataclass(frozen=True, slots=True)
class WorkspaceUserView:
    user: User
    roles: list[str]
    active_sessions: int


@dataclass(frozen=True, slots=True)
class ConnectionView:
    kind: str
    id: uuid.UUID
    status: str
    label: str | None
    store_id: uuid.UUID | None
    last_sync_at: datetime | None
    token_expires_at: datetime | None
    webhooks_registered_at: datetime | None
    last_error: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProductDetailView:
    product: Product
    variants: list[ProductVariant]
    images: list[str]
    listings: list[StoreListing]


@dataclass(frozen=True, slots=True)
class OrderDetailView:
    order: Order
    items: list[OrderItem]
    shipments: list[Shipment]
    events: list[OrderEvent]
    supplier_orders: list[SupplierOrder]


class PlatformWorkspaceService(BaseService):
    async def overview(self, tenant: Tenant) -> WorkspaceOverview:
        if require_tenant_id() != tenant.id:  # the dependency sets both; never trust drift
            raise RuntimeError("workspace context does not match the requested workspace")
        users = UserRepository(self.session)
        listings = StoreListingRepository(self.session)
        sub = await TenantSubscriptionRepository(self.session).current()
        return WorkspaceOverview(
            tenant=tenant,
            users=await users.count(),
            active_users=await users.count(filters={"is_active": True}),
            stores_by_status=await StoreRepository(self.session).count_by_status(),
            products=await ProductRepository(self.session).count_workspace(),
            orders_by_status=await OrderRepository(self.session).count_by_status(),
            listings_by_status={
                status: await listings.count(filters={"status": status})
                for status in ("pending", "synced", "error", "removed")
            },
            subscription=None
            if sub is None
            else SubscriptionSummary(
                plan=sub.plan,
                status=sub.status,
                ai_addon=sub.ai_addon,
                trial_ends_at=sub.trial_ends_at,
                current_period_end=sub.current_period_end,
                cancel_at_period_end=sub.cancel_at_period_end,
                has_stripe_customer=sub.stripe_customer_id is not None,
            ),
            health=await PlatformTenantDirectory(self.session).health(tenant.id),
        )

    # --- drill-down (phase 3) --------------------------------------------------

    async def users(
        self, params: ListQueryParams, *, active: bool | None
    ) -> tuple[list[WorkspaceUserView], int]:
        filters = None if active is None else {"is_active": active}
        rows, total = await UserRepository(self.session).list(params, filters=filters)
        ids = [u.id for u in rows]
        roles = await RoleRepository(self.session).role_names_for_users(ids)
        sessions = await RefreshTokenRepository(self.session).active_counts_for_users(ids)
        views = [
            WorkspaceUserView(
                user=u, roles=roles.get(u.id, []), active_sessions=sessions.get(u.id, 0)
            )
            for u in rows
        ]
        return views, total

    async def invitations(self, params: ListQueryParams) -> tuple[Sequence[UserInvitation], int]:
        return await UserInvitationRepository(self.session).list(params)

    async def stores(
        self, params: ListQueryParams, *, platform: str | None, status: str | None
    ) -> tuple[Sequence[Store], int]:
        filters: dict[str, Any] = {}
        if platform:
            filters["platform"] = _parse(StorePlatform, platform)
        if status:
            filters["status"] = _parse(StoreStatus, status)
        return await StoreRepository(self.session).list(params, filters=filters or None)

    async def connections(self) -> list[ConnectionView]:
        """Supplier and channel connections: their health, never their
        credentials."""
        out: list[ConnectionView] = []
        ali = await AliExpressConnectionRepository(self.session).get_for_tenant()
        if ali is not None:
            out.append(
                ConnectionView(
                    kind="aliexpress",
                    id=ali.id,
                    status=str(ali.status),
                    label=None,
                    store_id=None,
                    last_sync_at=ali.last_sync_at,
                    token_expires_at=ali.token_expiry,
                    webhooks_registered_at=None,
                    last_error=ali.last_error,
                    created_at=ali.created_at,
                )
            )
        for shop in await ShopifyConnectionRepository(self.session).list_all():
            out.append(
                ConnectionView(
                    kind="shopify",
                    id=shop.id,
                    status=str(shop.status),
                    label=shop.shop_domain,
                    store_id=shop.store_id,
                    last_sync_at=shop.last_sync_at,
                    token_expires_at=None,
                    webhooks_registered_at=shop.webhooks_registered_at,
                    last_error=shop.last_error,
                    created_at=shop.created_at,
                )
            )
        ebay = await EbayConnectionRepository(self.session).get_for_tenant()
        if ebay is not None:
            out.append(
                ConnectionView(
                    kind="ebay",
                    id=ebay.id,
                    status=str(ebay.status),
                    label=ebay.ebay_username or ebay.marketplace_id,
                    store_id=None,
                    last_sync_at=ebay.last_refreshed_at,
                    token_expires_at=ebay.refresh_token_expires_at,
                    webhooks_registered_at=None,
                    last_error=ebay.last_error or ebay.reconnect_reason,
                    created_at=ebay.created_at,
                )
            )
        return out

    async def products(
        self, params: ListQueryParams, *, publication: Literal["draft", "published"]
    ) -> tuple[Sequence[tuple[Product, int]], int]:
        return await ProductRepository(self.session).list_by_publication(
            params, publication=publication
        )

    async def product(self, product_id: uuid.UUID) -> ProductDetailView:
        product = await ProductRepository(self.session).get_by_id(product_id)
        if product is None:
            raise NotFoundError.for_resource("Product", product_id)
        variants, _ = await ProductVariantRepository(self.session).list(
            ListQueryParams(page=1, size=500, sort_by="created_at", sort_dir=SortDirection.ASC),
            filters={"product_id": product.id},
        )
        images, _ = await ProductImageRepository(self.session).list(
            ListQueryParams(page=1, size=100, sort_by="position", sort_dir=SortDirection.ASC),
            filters={"product_id": product.id},
        )
        listings = await StoreListingRepository(self.session).list_for_product(product.id)
        return ProductDetailView(
            product=product,
            variants=list(variants),
            images=[i.url for i in images],
            listings=list(listings),
        )

    async def listings(
        self, params: ListQueryParams, *, status: str | None, store_id: uuid.UUID | None
    ) -> tuple[Sequence[StoreListing], int]:
        filters: dict[str, Any] = {}
        if status:
            filters["status"] = _parse(ListingSyncStatus, status)
        if store_id:
            filters["store_id"] = store_id
        return await StoreListingRepository(self.session).list(params, filters=filters or None)

    async def orders(
        self,
        params: ListQueryParams,
        *,
        fulfillment_status: str | None,
        source: str | None,
    ) -> tuple[list[Order], int]:
        return await OrderRepository(self.session).list_orders(
            params,
            fulfillment_status=_parse(FulfillmentStatus, fulfillment_status)
            if fulfillment_status
            else None,
            source=_parse(OrderSource, source) if source else None,
        )

    async def order(self, order_id: uuid.UUID) -> OrderDetailView:
        order = await OrderRepository(self.session).get_by_id(order_id)
        if order is None:
            raise NotFoundError.for_resource("Order", order_id)
        page = ListQueryParams(page=1, size=500, sort_by="created_at", sort_dir=SortDirection.ASC)
        items, _ = await OrderItemRepository(self.session).list(
            page, filters={"order_id": order.id}
        )
        shipments, _ = await ShipmentRepository(self.session).list(
            page, filters={"order_id": order.id}
        )
        events, _ = await OrderEventRepository(self.session).list(
            ListQueryParams(page=1, size=500, sort_by="occurred_at", sort_dir=SortDirection.ASC),
            filters={"order_id": order.id},
        )
        supplier = await SupplierOrderRepository(self.session).for_order(order.id)
        return OrderDetailView(
            order=order,
            items=list(items),
            shipments=list(shipments),
            events=list(events),
            supplier_orders=[supplier] if supplier else [],
        )

    async def order_sync_runs(
        self, params: ListQueryParams, *, status: str | None
    ) -> tuple[Sequence[OrderSyncRun], int]:
        filters = {"status": _parse(SyncRunStatus, status)} if status else None
        return await OrderSyncRunRepository(self.session).list(params, filters=filters)

    async def inventory_sync_runs(
        self, params: ListQueryParams, *, status: str | None
    ) -> tuple[Sequence[InventorySyncRun], int]:
        filters = {"status": _parse(SyncRunStatus, status)} if status else None
        return await InventorySyncRunRepository(self.session).list(params, filters=filters)

    async def imports(
        self, params: ListQueryParams, *, status: str | None
    ) -> tuple[Sequence[ProductImport], int]:
        filters = {"status": _parse(ImportStatus, status)} if status else None
        return await ProductImportRepository(self.session).list(params, filters=filters)

    async def notifications(
        self, params: ListQueryParams, *, kind: str | None
    ) -> tuple[Sequence[Notification], int]:
        filters = {"kind": _parse(NotificationKind, kind)} if kind else None
        return await NotificationRepository(self.session).list(params, filters=filters)


@dataclass(frozen=True, slots=True)
class PlatformWorkspace:
    """An operator inside one workspace, for the length of one request."""

    principal: PlatformPrincipal
    tenant: Tenant


#: Roles an operator may give a member (D-019). ``owner`` is never given or
#: taken here: ownership transfer stays a merchant decision.
ASSIGNABLE_ROLES: frozenset[str] = frozenset({"admin", "member", "viewer"})


class PlatformWorkspaceActions(BaseService):
    """Changes an operator makes inside one workspace (phase 4 onwards).

    Every method runs inside the workspace's tenant context, behind
    ``platform_workspace(..., write=True)`` (permission, re-authentication,
    open support session), and writes its audit row in the same transaction
    as the change.
    """

    def __init__(
        self, session: AsyncSession, workspace: PlatformWorkspace, ctx: AuditContext
    ) -> None:
        super().__init__(session)
        self.workspace = workspace
        self.ctx = ctx
        self.users = UserRepository(session)

    async def _audit(
        self, action: str, *, target_type: str, target_id: uuid.UUID, reason: str, **detail: Any
    ) -> None:
        await PlatformAdminService(self.session).record_workspace_action(
            self.workspace.principal,
            self.workspace.tenant.id,
            action,
            target_type=target_type,
            target_id=str(target_id),
            reason=reason,
            ctx=self.ctx,
            detail=detail or None,
        )

    async def _failed(self, action: str, reason: str, exc: Exception) -> None:
        await PlatformAdminService(self.session).record_workspace_failure(
            self.workspace.principal,
            self.workspace.tenant.id,
            action,
            reason=reason,
            error=f"{type(exc).__name__}: {exc}",
            ctx=self.ctx,
        )

    async def user(self, user_id: uuid.UUID) -> User:
        # Tenant-scoped: another workspace's user is simply not found.
        user = await self.users.get_by_id(user_id)
        if user is None:
            raise NotFoundError.for_resource("User", user_id)
        return user

    async def _is_last_active_owner(self, user: User) -> bool:
        owners = await self.users.active_with_roles(("owner",))
        return [o.id for o in owners] == [user.id]

    async def set_user_active(self, user_id: uuid.UUID, *, active: bool, reason: str) -> User:
        user = await self.user(user_id)
        if user.is_active == active:
            return user
        if not active and await self._is_last_active_owner(user):
            raise ConflictError(
                "This is the workspace's only active owner; disabling them would lock "
                "the workspace. Suspend the workspace instead."
            )
        await self.users.update(user, is_active=active)
        ended = (
            0 if active else await RefreshTokenRepository(self.session).revoke_all_for_user(user.id)
        )
        await self._audit(
            "workspace_user_enabled" if active else "workspace_user_disabled",
            target_type="user",
            target_id=user.id,
            reason=reason,
            sessions_ended=ended,
        )
        return user

    async def end_user_sessions(self, user_id: uuid.UUID, *, reason: str) -> int:
        user = await self.user(user_id)
        ended = await RefreshTokenRepository(self.session).revoke_all_for_user(user.id)
        await self._audit(
            "workspace_user_sessions_ended",
            target_type="user",
            target_id=user.id,
            reason=reason,
            sessions_ended=ended,
        )
        return ended

    async def require_password_reset(self, user_id: uuid.UUID, *, reason: str) -> None:
        """The password stops working and every session ends. Sign-in then
        fails exactly like a wrong password (no new state an attacker could
        detect); the user sets a new one with "Forgot password", and a
        Google sign-in, if linked, keeps working."""
        user = await self.user(user_id)
        had_password = user.password_hash is not None
        await self.users.update(user, password_hash=None)
        ended = await RefreshTokenRepository(self.session).revoke_all_for_user(user.id)
        await self._audit(
            "workspace_user_password_reset_required",
            target_type="user",
            target_id=user.id,
            reason=reason,
            had_password=had_password,
            sessions_ended=ended,
        )

    async def set_member_role(self, user_id: uuid.UUID, *, role: str, reason: str) -> list[str]:
        if role not in ASSIGNABLE_ROLES:
            raise ValidationError("An operator can give only admin, member or viewer.")
        user = await self.user(user_id)
        roles = RoleRepository(self.session)
        before = sorted(await roles.list_role_names_for_user(user.id))
        if "owner" in before:
            raise ConflictError("An owner's role is not changed from the console.")
        if before == [role]:
            return before
        for name in before:
            existing = await roles.get_by_name(name)
            if existing is not None:
                await roles.revoke(user_id=user.id, role_id=existing.id)
        await roles.assign_by_name(user_id=user.id, name=RoleName(role))
        # The new role applies at the next token refresh; ending the sessions
        # makes it apply now, as a role change should.
        ended = await RefreshTokenRepository(self.session).revoke_all_for_user(user.id)
        await self._audit(
            "workspace_user_role_changed",
            target_type="user",
            target_id=user.id,
            reason=reason,
            before={"roles": before},
            after={"roles": [role]},
            sessions_ended=ended,
        )
        return [role]

    async def revoke_invitation(self, invitation_id: uuid.UUID, *, reason: str) -> None:
        await TeamInvitationService(self.session).revoke(invitation_id)
        await self._audit(
            "workspace_invitation_revoked",
            target_type="invitation",
            target_id=invitation_id,
            reason=reason,
        )


class PlatformStoreActions(PlatformWorkspaceActions):
    """Store and integration changes (phase 5, ``stores.manage``). Each one
    reuses the merchant's own service, so there is one implementation of a
    sync or a webhook registration, not a second "admin" copy."""

    async def _store(self, store_id: uuid.UUID) -> Store:
        store = await StoreRepository(self.session).get_by_id(store_id)
        if store is None:
            raise NotFoundError.for_resource("Store", store_id)
        return store

    async def set_store_paused(self, store_id: uuid.UUID, *, paused: bool, reason: str) -> Store:
        store = await self._store(store_id)
        if (store.sync_paused_at is not None) == paused:
            return store
        await StoreRepository(self.session).update(
            store,
            sync_paused_at=datetime.now(UTC) if paused else None,
            sync_paused_reason=reason[:500] if paused else None,
        )
        await NotificationRepository(self.session).create(
            kind=NotificationKind.INFO,
            title=(
                f"DropPilot support paused updates to {store.name}"
                if paused
                else f"DropPilot support resumed updates to {store.name}"
            ),
            body=(
                f"Prices, stock and new listings are not sent to this store until the "
                f"pause is lifted. Orders keep arriving. Reason: {reason[:300]}"
                if paused
                else "Prices, stock and new listings are sent to this store again."
            ),
            payload={"store_id": str(store.id)},
        )
        await self._audit(
            "workspace_store_paused" if paused else "workspace_store_resumed",
            target_type="store",
            target_id=store.id,
            reason=reason,
        )
        return store

    async def sync_orders_now(self, *, reason: str) -> OrderSyncRun:
        """The supplier order sync the merchant's own "Sync now" runs.
        A run already in flight is a 409, as for them."""
        try:
            run = await OrderSyncService(self.session).sync_orders(trigger=SyncTrigger.MANUAL)
        except Exception as exc:
            await self._failed("workspace_order_sync_started", reason, exc)
            raise
        await self._audit(
            "workspace_order_sync_started",
            target_type="sync_run",
            target_id=run.id,
            reason=reason,
            status=str(run.status),
        )
        return run

    async def sync_inventory_now(
        self, *, store_id: uuid.UUID | None, reason: str
    ) -> tuple[InventorySyncRun, list[uuid.UUID]]:
        if store_id is not None:
            await self._store(store_id)
        service = InventorySyncService(self.session)
        try:
            run = await service.sync(store_id=store_id, trigger=SyncTrigger.MANUAL)
        except Exception as exc:
            await self._failed("workspace_inventory_sync_started", reason, exc)
            raise
        await self._audit(
            "workspace_inventory_sync_started",
            target_type="sync_run",
            target_id=run.id,
            reason=reason,
            status=str(run.status),
            store_id=str(store_id) if store_id else None,
        )
        return run, list(service.changed_product_ids)

    async def register_shopify_webhooks(self, store_id: uuid.UUID, *, reason: str) -> bool:
        """The reconciler OAuth and the merchant's "retry" both use. Returns
        whether every required webhook is now registered."""
        await self._store(store_id)
        try:
            report = await ShopifyService(self.session).register_webhooks(store_id)
        except Exception as exc:
            await self._failed("workspace_shopify_webhooks_reconciled", reason, exc)
            raise
        await self._audit(
            "workspace_shopify_webhooks_reconciled",
            target_type="store",
            target_id=store_id,
            reason=reason,
            healthy=report.healthy,
            created=report.created_count,
        )
        return report.healthy


class PlatformCatalogActions(PlatformWorkspaceActions):
    """Catalogue and order changes (phase 6). Each reuses the merchant's own
    service and its guards."""

    async def retry_import(self, import_id: uuid.UUID, *, reason: str) -> Product:
        """Only a failed import can be retried, exactly as for the merchant."""
        try:
            product = await ProductImportService(self.session).retry_import(import_id)
        except Exception as exc:
            await self._failed("workspace_import_retried", reason, exc)
            raise
        await self._audit(
            "workspace_import_retried",
            target_type="product_import",
            target_id=import_id,
            reason=reason,
            product_id=str(product.id),
        )
        return product

    async def resync_listings(self, product_id: uuid.UUID, *, reason: str) -> int:
        """Queue the price and stock push to every channel listing of one
        product (after this request commits; paused stores are skipped by the
        push itself). Returns the number of listings that will be tried."""
        product = await ProductRepository(self.session).get_by_id(product_id)
        if product is None:
            raise NotFoundError.for_resource("Product", product_id)
        listings = await StoreListingRepository(self.session).list_for_product(product.id)
        await self._audit(
            "workspace_listings_resync_queued",
            target_type="product",
            target_id=product.id,
            reason=reason,
            listings=len(listings),
        )
        return len(listings)

    async def refresh_order(self, order_id: uuid.UUID, *, reason: str) -> Order:
        order = await OrderRepository(self.session).get_by_id(order_id)
        if order is None:
            raise NotFoundError.for_resource("Order", order_id)
        try:
            order = await OrderSyncService(self.session).refresh_order(order)
        except Exception as exc:
            await self._failed("workspace_order_refreshed", reason, exc)
            raise
        await self._audit(
            "workspace_order_refreshed", target_type="order", target_id=order.id, reason=reason
        )
        return order

    async def release_supplier_order(self, order_id: uuid.UUID, *, reason: str) -> SupplierOrder:
        """The merchant's own release (D-017): only from ``placing``, after
        someone has checked AliExpress and found no order. Recorded as done by
        support, not by the merchant."""
        row = await SupplierOrderingService(self.session).release(order_id)
        row = await SupplierOrderRepository(self.session).update(
            row,
            error_code="released_by_support",
            error_message=(
                "Released by DropPilot support after checking AliExpress: "
                "no order had been created."
            ),
        )
        await self._audit(
            "workspace_supplier_order_released",
            target_type="order",
            target_id=order_id,
            reason=reason,
        )
        return row


class PlatformJobActions(PlatformWorkspaceActions):
    """Background work in one workspace (phase 7, ``jobs.manage``)."""

    async def close_stuck_sync(
        self, kind: Literal["order_sync", "inventory_sync"], run_id: uuid.UUID, *, reason: str
    ) -> OrderSyncRun | InventorySyncRun:
        """A sync run left ``running`` blocks every later sync of the
        workspace (``ConflictError`` on start) and nothing else ever clears
        it. Closing one marks it failed; it does not touch what the run had
        already written. Only a run past the stuck threshold can be closed,
        so a live run is never cut off."""
        repo: OrderSyncRunRepository | InventorySyncRunRepository = (
            OrderSyncRunRepository(self.session)
            if kind == "order_sync"
            else InventorySyncRunRepository(self.session)
        )
        run = await repo.get_by_id(run_id)
        if run is None:
            raise NotFoundError.for_resource("Sync run", run_id)
        now = datetime.now(UTC)
        if run.status != SyncRunStatus.RUNNING or (
            run.started_at is not None and run.started_at > now - RUN_STUCK_AFTER
        ):
            raise ConflictError(
                "Only a run still running after "
                f"{int(RUN_STUCK_AFTER.total_seconds() // 60)} minutes can be closed."
            )
        run.status = SyncRunStatus.FAILED
        run.finished_at = now
        run.error_code = "closed_by_support"
        run.error_message = "Closed by DropPilot support: the run had stopped responding."
        await self.session.flush()
        await self._audit(
            "workspace_sync_run_closed",
            target_type=kind,
            target_id=run.id,
            reason=reason,
        )
        return run

    async def cancel_pipeline_run(self, run_id: uuid.UUID, *, reason: str) -> str:
        """The merchant's own cancel, which never waits on a worker's lock:
        returns ``cancelled`` or ``requested`` (the worker stops at its next
        item)."""
        outcome = await PipelineBulkRunService(self.session).cancel(run_id)
        state = "requested" if outcome.cancel_requested_at else "cancelled"
        await self._audit(
            "workspace_pipeline_run_cancelled",
            target_type="pipeline_run",
            target_id=run_id,
            reason=reason,
            outcome=state,
        )
        return state

    async def cancel_rule_application(self, application_id: uuid.UUID, *, reason: str) -> str:
        """The merchant's own cancel: total if pending, cooperative if
        running; batches already written keep their prices."""
        application = await RuleApplicationService(self.session).cancel(application_id)
        await self._audit(
            "workspace_rule_application_cancelled",
            target_type="rule_application",
            target_id=application_id,
            reason=reason,
        )
        return str(application.status)

    async def retry_automation(self, run_id: uuid.UUID, *, reason: str) -> AutomationRun:
        """Run the rule behind a failed automation run once more."""
        previous = await AutomationRunRepository(self.session).get_by_id(run_id)
        if previous is None:
            raise NotFoundError.for_resource("Automation run", run_id)
        try:
            run = await AutomationService(self.session).run_rule(previous.rule_id, trigger="manual")
        except Exception as exc:
            await self._failed("workspace_automation_retried", reason, exc)
            raise
        await self._audit(
            "workspace_automation_retried",
            target_type="automation_run",
            target_id=run.id,
            reason=reason,
            previous_run_id=str(previous.id),
            status=str(run.status),
        )
        return run


__all__ = [
    "ASSIGNABLE_ROLES",
    "ConnectionView",
    "OrderDetailView",
    "PlatformCatalogActions",
    "PlatformJobActions",
    "PlatformStoreActions",
    "PlatformWorkspace",
    "PlatformWorkspaceActions",
    "PlatformWorkspaceService",
    "ProductDetailView",
    "SubscriptionSummary",
    "WorkspaceOverview",
    "WorkspaceUserView",
]
