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
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from app.core.context import require_tenant_id
from app.core.exceptions import NotFoundError, ValidationError
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
)
from app.models.product import Product, ProductVariant
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.supplier_order import SupplierOrder
from app.models.tenant import Tenant
from app.models.user import User
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
from app.repositories.product import (
    ProductImageRepository,
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
from app.services.base import BaseService
from app.services.platform_admin import PlatformPrincipal


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


__all__ = [
    "ConnectionView",
    "OrderDetailView",
    "PlatformWorkspace",
    "PlatformWorkspaceService",
    "ProductDetailView",
    "SubscriptionSummary",
    "WorkspaceOverview",
    "WorkspaceUserView",
]
