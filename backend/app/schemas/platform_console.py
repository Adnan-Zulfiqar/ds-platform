"""Response schemas for the operator console's data views (D-019).

Separate from ``platform_admin`` (operator identity) because these describe
workspace data. Every model lists its fields explicitly: an encrypted
credential, a password hash or a token hash has no field here, so it cannot
reach a response even by accident.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.schemas.base import CamelCaseModel


class DailyCountRead(CamelCaseModel):
    day: date
    count: int


class SystemHealthRead(CamelCaseModel):
    database: bool
    redis: bool
    migration_revision: str | None


class PlatformDashboardRead(CamelCaseModel):
    generated_at: datetime
    tenants_by_status: dict[str, int]
    tenants_new_week: int
    tenants_new_month: int
    users_active: int
    users_new_week: int
    stores_by_status: dict[str, int]
    stores_by_platform: dict[str, int]
    products_total: int
    listings_by_status: dict[str, int]
    orders_last_day: int
    orders_last_week: int
    subscriptions_by_plan: dict[str, int]
    subscriptions_by_status: dict[str, int]
    trials_ending_week: int
    failed_last_day: dict[str, int]
    stuck: dict[str, int]
    operator_sessions_open: int
    security_failures_last_day: int
    signups_by_day: list[DailyCountRead]
    orders_by_day: list[DailyCountRead]
    system: SystemHealthRead


class SubscriptionSummaryRead(CamelCaseModel):
    plan: str | None
    status: str
    ai_addon: bool
    trial_ends_at: datetime
    current_period_end: datetime | None
    cancel_at_period_end: bool
    has_stripe_customer: bool


class WorkspaceHealthRead(CamelCaseModel):
    window_hours: int
    failed_order_syncs: int
    failed_inventory_syncs: int
    listings_in_error: int
    failed_notification_emails: int


class WorkspaceOverviewRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    is_active: bool
    timezone: str | None
    default_currency: str | None
    created_at: datetime
    users: int
    active_users: int
    stores_by_status: dict[str, int]
    products: dict[str, int]
    orders_by_status: dict[str, int]
    listings_by_status: dict[str, int]
    subscription: SubscriptionSummaryRead | None
    health: WorkspaceHealthRead


# --- Workspace drill-down (phase 3) -------------------------------------------


class WorkspaceUserRead(CamelCaseModel):
    id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime
    roles: list[str]
    active_sessions: int


class WorkspaceInvitationRead(CamelCaseModel):
    id: uuid.UUID
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class WorkspaceStoreRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    slug: str
    platform: str
    status: str
    storefront_url: str | None
    currency: str
    inventory_sync_enabled: bool
    pricing_sync_enabled: bool
    order_sync_enabled: bool
    last_sync_at: datetime | None
    last_activity_at: datetime | None
    last_error: str | None
    health_score: int
    sync_paused_at: datetime | None
    sync_paused_reason: str | None
    created_at: datetime


class WorkspaceConnectionRead(CamelCaseModel):
    """One supplier or channel connection. Credentials are never fields:
    only whether the connection is usable and when it last worked."""

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


class WorkspaceProductRead(CamelCaseModel):
    id: uuid.UUID
    title: str
    status: str
    source: str
    external_id: str
    supplier_name: str | None
    currency: str | None
    cost_price_min: Decimal | None
    sell_price: Decimal | None
    stock_quantity: int
    ai_status: str
    needs_review: bool
    variant_count: int
    last_synced_at: datetime | None
    last_sync_error: str | None
    created_at: datetime
    updated_at: datetime


class WorkspaceVariantRead(CamelCaseModel):
    id: uuid.UUID
    label: str | None
    merchant_sku: str | None
    external_variant_id: str
    cost_price: Decimal | None
    sell_price: Decimal | None
    currency: str | None
    stock_quantity: int
    is_enabled: bool


class WorkspaceListingRead(CamelCaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    store_id: uuid.UUID
    status: str
    external_product_id: str
    shop_domain: str | None
    storefront_url: str | None
    published_at: datetime | None
    last_synced_at: datetime | None
    last_failed_sync_at: datetime | None
    last_error: str | None
    created_at: datetime


class WorkspaceProductDetailRead(WorkspaceProductRead):
    description: str | None
    external_url: str | None
    category_name: str | None
    tags: list[str]
    images: list[str]
    variants: list[WorkspaceVariantRead]
    listings: list[WorkspaceListingRead]


class WorkspaceOrderRead(CamelCaseModel):
    id: uuid.UUID
    source: str
    external_id: str
    store_id: uuid.UUID | None
    fulfillment_status: str
    payment_status: str
    buyer_name: str | None
    buyer_country: str | None
    currency: str | None
    total_amount: Decimal | None
    external_created_at: datetime | None
    last_synced_at: datetime | None
    last_sync_error: str | None
    created_at: datetime


class WorkspaceOrderItemRead(CamelCaseModel):
    id: uuid.UUID
    title: str | None
    sku_attributes: str | None
    quantity: int
    unit_price: Decimal | None
    currency: str | None
    product_id: uuid.UUID | None


class WorkspaceShipmentRead(CamelCaseModel):
    id: uuid.UUID
    status: str
    carrier: str | None
    tracking_number: str | None
    estimated_delivery_at: datetime | None
    created_at: datetime


class WorkspaceOrderEventRead(CamelCaseModel):
    id: uuid.UUID
    event_type: str
    from_status: str | None
    to_status: str | None
    description: str | None
    occurred_at: datetime


class WorkspaceSupplierOrderRead(CamelCaseModel):
    id: uuid.UUID
    status: str
    trigger: str
    review_reasons: list[str]
    external_order_ids: list[str]
    error_code: str | None
    error_message: str | None
    placed_at: datetime | None
    tracking_number: str | None
    tracking_carrier: str | None
    tracking_pushed_at: datetime | None
    created_at: datetime


class WorkspaceOrderDetailRead(WorkspaceOrderRead):
    recipient_name: str | None
    recipient_phone: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    province: str | None
    postal_code: str | None
    country_code: str | None
    shipping_amount: Decimal | None
    paid_at: datetime | None
    delivered_at: datetime | None
    items: list[WorkspaceOrderItemRead]
    shipments: list[WorkspaceShipmentRead]
    events: list[WorkspaceOrderEventRead]
    supplier_orders: list[WorkspaceSupplierOrderRead]


class WorkspaceSyncRunRead(CamelCaseModel):
    """An order or inventory sync run: what background work did."""

    kind: str
    id: uuid.UUID
    status: str
    trigger: str
    store_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class WorkspaceNotificationRead(CamelCaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    user_id: uuid.UUID | None
    is_read: bool
    email_status: str | None
    created_at: datetime


# --- Support sessions and workspace changes (phase 4) -------------------------


class SupportSessionOpen(CamelCaseModel):
    reason: str = Field(min_length=3, max_length=500)
    minutes: int = Field(default=30, ge=5, le=120)


class SupportSessionRead(CamelCaseModel):
    id: uuid.UUID
    reason: str
    created_at: datetime
    expires_at: datetime
    ended_at: datetime | None


class WorkspaceChangeReason(CamelCaseModel):
    """Every change inside a workspace says why; the reason is audited."""

    reason: str = Field(min_length=3, max_length=500)


class WorkspaceRoleChange(WorkspaceChangeReason):
    role: Literal["admin", "member", "viewer"]


class WorkspaceUserChanged(CamelCaseModel):
    id: uuid.UUID
    is_active: bool
    roles: list[str]
    sessions_ended: int = 0


# --- Store and integration changes (phase 5) ----------------------------------


class InventorySyncNow(WorkspaceChangeReason):
    store_id: uuid.UUID | None = None


class WebhookReconcileResult(CamelCaseModel):
    store_id: uuid.UUID
    healthy: bool


# --- Catalogue and orders (phase 6) -------------------------------------------


class WorkspaceImportRead(CamelCaseModel):
    id: uuid.UUID
    source: str
    external_id: str
    status: str
    product_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    ship_to_country: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ListingsResyncQueued(CamelCaseModel):
    product_id: uuid.UUID
    listings: int


# --- Jobs (phase 7) ---------------------------------------------------------------


class PlatformJobRead(CamelCaseModel):
    kind: str
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    status: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class JobActionResult(CamelCaseModel):
    kind: str
    id: uuid.UUID
    outcome: str


__all__ = [
    "DailyCountRead",
    "InventorySyncNow",
    "JobActionResult",
    "ListingsResyncQueued",
    "PlatformDashboardRead",
    "PlatformJobRead",
    "SubscriptionSummaryRead",
    "SupportSessionOpen",
    "SupportSessionRead",
    "SystemHealthRead",
    "WebhookReconcileResult",
    "WorkspaceChangeReason",
    "WorkspaceConnectionRead",
    "WorkspaceHealthRead",
    "WorkspaceImportRead",
    "WorkspaceInvitationRead",
    "WorkspaceListingRead",
    "WorkspaceNotificationRead",
    "WorkspaceOrderDetailRead",
    "WorkspaceOrderEventRead",
    "WorkspaceOrderItemRead",
    "WorkspaceOrderRead",
    "WorkspaceOverviewRead",
    "WorkspaceProductDetailRead",
    "WorkspaceProductRead",
    "WorkspaceRoleChange",
    "WorkspaceShipmentRead",
    "WorkspaceStoreRead",
    "WorkspaceSupplierOrderRead",
    "WorkspaceSyncRunRead",
    "WorkspaceUserChanged",
    "WorkspaceUserRead",
    "WorkspaceVariantRead",
]
