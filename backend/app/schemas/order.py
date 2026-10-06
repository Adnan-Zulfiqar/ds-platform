"""Order API schemas.

What the platform returns to its own clients, as distinct from the wire models
in ``integrations.aliexpress.orders`` that describe what a supplier sends.

Buyer contact detail is trimmed to what the UI genuinely renders. The full
shipping address exists on the detail response because fulfilment support needs
it; the list response carries only the destination country, because a table of
orders has no business paging full addresses across the wire.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.models.order import (
    FulfillmentStatus,
    OrderEventType,
    OrderSource,
    PaymentStatus,
    ShipmentStatus,
    SyncRunStatus,
    SyncTrigger,
)
from app.schemas.base import CamelCaseModel


class OrderItemRead(CamelCaseModel):
    """One line of an order."""

    id: uuid.UUID
    external_item_id: str | None = None
    product_id: uuid.UUID | None = None
    external_product_id: str | None = None
    title: str | None = None
    sku_attributes: str | None = None
    quantity: int
    unit_price: Decimal | None = None
    currency: str | None = None
    external_status: str | None = None


class TrackingEventRead(CamelCaseModel):
    """One carrier scan."""

    id: uuid.UUID
    occurred_at: datetime
    status: str | None = None
    description: str | None = None
    location: str | None = None


class ShipmentRead(CamelCaseModel):
    """A shipment with its tracking history."""

    id: uuid.UUID
    tracking_number: str | None = None
    carrier: str | None = None
    service_name: str | None = None
    status: ShipmentStatus
    estimated_delivery_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    current_location: str | None = None
    last_checked_at: datetime | None = None
    tracking_events: list[TrackingEventRead] = Field(default_factory=list)


class OrderRead(CamelCaseModel):
    """An order in list form.

    Items, shipments and the address are omitted here and returned only by the
    detail endpoint — the same list/detail split as the catalogue, for the same
    reason.
    """

    id: uuid.UUID
    source: OrderSource
    external_id: str
    external_status: str | None = None
    fulfillment_status: FulfillmentStatus
    payment_status: PaymentStatus
    buyer_name: str | None = None
    country_code: str | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    item_count: int = 0
    external_created_at: datetime | None = None
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime


class OrderDetailRead(OrderRead):
    """A single order, with everything needed to render its page."""

    buyer_country: str | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None
    shipping_amount: Decimal | None = None
    paid_at: datetime | None = None
    delivered_at: datetime | None = None
    items: list[OrderItemRead] = Field(default_factory=list)
    shipments: list[ShipmentRead] = Field(default_factory=list)


class OrderTimelineEntryRead(CamelCaseModel):
    """One moment in an order's history.

    A merged view: order lifecycle events and carrier tracking scans arrive
    from different tables but the reader wants one chronological story, so the
    ``kind`` discriminator says which table a row came from.
    """

    kind: Literal["order", "tracking"]
    event_type: OrderEventType | None = None
    from_status: str | None = None
    to_status: str | None = None
    status: str | None = None
    description: str | None = None
    location: str | None = None
    occurred_at: datetime


class OrderSyncRequest(CamelCaseModel):
    """Ask for a synchronisation run.

    ``since_days`` bounds the window the supplier is asked for. Capped low
    because each day of window is more pages of quota; a backfill beyond the
    cap is an operational task, not a button.
    """

    since_days: int = Field(default=7, ge=1, le=90)


class OrderSyncRunRead(CamelCaseModel):
    """One synchronisation attempt — the audit record."""

    id: uuid.UUID
    trigger: SyncTrigger
    status: SyncRunStatus
    window_start: datetime | None = None
    window_end: datetime | None = None
    orders_seen: int
    orders_created: int
    orders_updated: int
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class OrderStatisticsRead(CamelCaseModel):
    """The live status dashboard payload.

    Everything here is computed from this platform's own tables. What the
    platform cannot honestly report — broker queue depth without a broker
    connection, webhook activity beyond what Redis has counted — is absent
    rather than fabricated.
    """

    total_orders: int
    by_status: dict[str, int]
    pending_fulfillment: int
    processing: int
    delivered: int
    failed_syncs_last_7_days: int
    last_sync: OrderSyncRunRead | None = None
    webhook_events_received: int | None = Field(
        default=None,
        description=(
            "Webhook deliveries counted since Redis last restarted; null when Redis is unavailable."
        ),
    )


__all__ = [
    "FulfilmentSettingsRead",
    "FulfilmentSettingsUpdate",
    "OrderDetailRead",
    "OrderItemRead",
    "OrderRead",
    "OrderStatisticsRead",
    "OrderSyncRequest",
    "OrderSyncRunRead",
    "OrderTimelineEntryRead",
    "ShipmentRead",
    "SupplierOrderRead",
    "TrackingEventRead",
]


# --- Supplier orders (Track F, D-017) -----------------------------------------


class SupplierOrderRead(CamelCaseModel):
    """The AliExpress order behind a channel order. No address: that stays on
    the order and is never echoed back here."""

    status: Literal["none", "needs_review", "queued", "placing", "placed", "failed", "shipped"]
    trigger: str | None = None
    review_reasons: list[str] = Field(default_factory=list)
    external_order_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    placed_at: datetime | None = None
    tracking_number: str | None = None
    tracking_carrier: str | None = None
    tracking_pushed_at: datetime | None = None
    #: Where the merchant pays the unpaid AliExpress order.
    payment_url: str | None = None


class FulfilmentSettingsRead(CamelCaseModel):
    auto_order: bool
    auto_tracking: bool
    fallback_shipping_method: str | None


class FulfilmentSettingsUpdate(CamelCaseModel):
    auto_order: bool
    auto_tracking: bool
    fallback_shipping_method: str | None = Field(default=None, max_length=128)
