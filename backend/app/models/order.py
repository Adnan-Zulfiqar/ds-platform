"""Order management domain.

The domain model for imported and tracked orders. **Nothing here imports the
AliExpress package**, and no column is named after an AliExpress field — the
integration adapter translates their payload into these columns, exactly as the
catalogue does. A second marketplace lands in the same tables with a different
``source``.

Money is ``Numeric``, never a float column, for the same reason as the
catalogue: a float column loses exactness at the database boundary no matter
how careful the application is above it.

**Buyer data is minimised deliberately.** Only what fulfilment genuinely needs
is stored — a recipient and a destination. The platform must not become a
warehouse for buyer PII it has no use for; every retained field is one more
thing a breach can leak.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantScopedBase

#: Same money precision as the catalogue — see ``models/product.py``.
_MONEY = Numeric(16, 4)


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """A value-persisting Postgres enum.

    ``values_callable`` is mandatory here: SQLAlchemy's default persists member
    *names* ("PENDING"), which do not match the lowercase labels the migration
    creates, and the mismatch fails every insert at runtime.
    """
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda cls: [member.value for member in cls],
    )


class OrderSource(StrEnum):
    """Where an order came from."""

    ALIEXPRESS = "aliexpress"
    MANUAL = "manual"


class FulfillmentStatus(StrEnum):
    """Where an order sits in its fulfilment lifecycle.

    The full vocabulary the platform recognises. Supplier statuses are mapped
    into this set by the integration adapter; the raw supplier string is kept
    alongside in ``external_status`` so nothing is lost in translation.
    """

    PENDING = "pending"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    PROCESSING = "processing"
    FULFILLED = "fulfilled"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    DISPUTED = "disputed"


#: The allowed forward moves. Everything else is a validation error.
#:
#: Lives beside the enum rather than in the service so there is exactly one
#: authority on what the lifecycle permits — a service method and a Celery task
#: consult the same map and cannot drift.
#:
#: ``CANCELLED`` and ``REFUNDED`` are terminal. ``DISPUTED`` can resolve in
#: three directions because that is what disputes do: the buyer wins (refund),
#: the seller wins (delivered stands), or the order is unwound (cancelled).
FULFILLMENT_TRANSITIONS: dict[FulfillmentStatus, frozenset[FulfillmentStatus]] = {
    FulfillmentStatus.PENDING: frozenset(
        {
            FulfillmentStatus.AWAITING_PAYMENT,
            FulfillmentStatus.PAID,
            FulfillmentStatus.CANCELLED,
        }
    ),
    FulfillmentStatus.AWAITING_PAYMENT: frozenset(
        {FulfillmentStatus.PAID, FulfillmentStatus.CANCELLED}
    ),
    FulfillmentStatus.PAID: frozenset(
        {
            FulfillmentStatus.PROCESSING,
            FulfillmentStatus.CANCELLED,
            FulfillmentStatus.REFUNDED,
            FulfillmentStatus.DISPUTED,
        }
    ),
    FulfillmentStatus.PROCESSING: frozenset(
        {
            FulfillmentStatus.FULFILLED,
            FulfillmentStatus.CANCELLED,
            FulfillmentStatus.REFUNDED,
            FulfillmentStatus.DISPUTED,
        }
    ),
    FulfillmentStatus.FULFILLED: frozenset(
        {
            FulfillmentStatus.SHIPPED,
            FulfillmentStatus.REFUNDED,
            FulfillmentStatus.DISPUTED,
        }
    ),
    FulfillmentStatus.SHIPPED: frozenset(
        {
            FulfillmentStatus.DELIVERED,
            FulfillmentStatus.REFUNDED,
            FulfillmentStatus.DISPUTED,
        }
    ),
    FulfillmentStatus.DELIVERED: frozenset(
        {FulfillmentStatus.REFUNDED, FulfillmentStatus.DISPUTED}
    ),
    FulfillmentStatus.CANCELLED: frozenset(),
    FulfillmentStatus.REFUNDED: frozenset(),
    FulfillmentStatus.DISPUTED: frozenset(
        {
            FulfillmentStatus.REFUNDED,
            FulfillmentStatus.CANCELLED,
            FulfillmentStatus.DELIVERED,
        }
    ),
}


def can_transition(current: FulfillmentStatus, target: FulfillmentStatus) -> bool:
    """Whether the lifecycle permits moving from ``current`` to ``target``.

    Setting the same status again is permitted — a sync confirming the current
    state is a no-op, not a violation.
    """
    if current == target:
        return True
    return target in FULFILLMENT_TRANSITIONS.get(current, frozenset())


class PaymentStatus(StrEnum):
    """Payment state, tracked separately from fulfilment.

    ``UNKNOWN`` is a real value, not a placeholder: a supplier payload that
    omits payment information must be recorded as "not reported" rather than
    guessed into "unpaid", which would look actionable when it is not.
    """

    UNKNOWN = "unknown"
    UNPAID = "unpaid"
    PAID = "paid"
    REFUNDED = "refunded"


class ShipmentStatus(StrEnum):
    """Where a shipment sits in transit."""

    PENDING = "pending"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    EXCEPTION = "exception"
    UNKNOWN = "unknown"


class OrderEventType(StrEnum):
    """What kind of moment a timeline entry records."""

    CREATED = "created"
    STATUS_CHANGE = "status_change"
    SYNCED = "synced"
    SHIPMENT = "shipment"
    WEBHOOK = "webhook"


class SyncRunStatus(StrEnum):
    """Outcome of one synchronisation run."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class SyncTrigger(StrEnum):
    """What started a synchronisation run.

    Recorded because the answer to "why did this sync run at 03:00" matters
    when diagnosing quota exhaustion or an unexpected data change.
    """

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    WEBHOOK = "webhook"


class Order(TenantScopedBase):
    """A customer order, imported from a supplier or entered manually."""

    __tablename__ = "orders"

    __table_args__ = (
        # The idempotency guarantee, identical in shape to the catalogue's:
        # syncing the same supplier order twice updates rather than duplicates,
        # enforced by the database rather than by call-site discipline.
        UniqueConstraint(
            "tenant_id",
            "source",
            "external_id",
            name="uq_orders_tenant_source_external",
        ),
        Index("ix_orders_tenant_fulfillment", "tenant_id", "fulfillment_status"),
        Index("ix_orders_tenant_external_created", "tenant_id", "external_created_at"),
    )

    # --- Provenance ---------------------------------------------------------
    source: Mapped[OrderSource] = mapped_column(
        _enum(OrderSource, "order_source"),
        nullable=False,
        default=OrderSource.ALIEXPRESS,
    )

    #: The supplier's order identifier, as a string — same reasoning as
    #: ``products.external_id``: AliExpress uses integers, others use opaque
    #: strings, and the widest representation wins.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The supplier's own status string, verbatim. The mapped
    #: ``fulfillment_status`` is this platform's vocabulary; keeping the raw
    #: value means a mapping mistake is recoverable rather than destructive.
    external_status: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # --- Lifecycle ------------------------------------------------------------
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        _enum(FulfillmentStatus, "fulfillment_status"),
        nullable=False,
        default=FulfillmentStatus.PENDING,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        _enum(PaymentStatus, "order_payment_status"),
        nullable=False,
        default=PaymentStatus.UNKNOWN,
    )

    # --- Buyer & destination (minimised) -------------------------------------
    buyer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buyer_country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(512), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(512), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    province: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # --- Money ----------------------------------------------------------------
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    shipping_amount: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    # --- Supplier timestamps ----------------------------------------------------
    external_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Sync bookkeeping -------------------------------------------------------
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # --- Children -----------------------------------------------------------
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="OrderItem.created_at",
    )
    shipments: Mapped[list[Shipment]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Shipment.created_at",
    )
    events: Mapped[list[OrderEvent]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="OrderEvent.occurred_at",
    )

    def __repr__(self) -> str:
        return (
            f"<Order id={self.id} external_id={self.external_id} status={self.fulfillment_status}>"
        )


class OrderItem(TenantScopedBase):
    """One line of an order."""

    __tablename__ = "order_items"

    __table_args__ = (Index("ix_order_items_tenant_order", "tenant_id", "order_id"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: The supplier's line identifier, when it provides one.
    external_item_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Link to the imported catalogue product, when one matches. SET NULL: a
    #: product removed from the catalogue must not delete order history.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    #: The composite variant key, verbatim — the same ``sku_attr`` the
    #: catalogue stores, because an order echoes it back exactly.
    sku_attributes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    #: The supplier's per-line status, verbatim.
    external_status: Mapped[str | None] = mapped_column(String(128), nullable=True)

    order: Mapped[Order] = relationship(back_populates="items")


class Shipment(TenantScopedBase):
    """A physical dispatch against an order.

    One order can ship in several parcels, so this is a child table rather than
    columns on the order.
    """

    __tablename__ = "shipments"

    __table_args__ = (
        Index("ix_shipments_tenant_order", "tenant_id", "order_id"),
        Index("ix_shipments_tenant_tracking", "tenant_id", "tracking_number"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    tracking_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    carrier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[ShipmentStatus] = mapped_column(
        _enum(ShipmentStatus, "shipment_status"),
        nullable=False,
        default=ShipmentStatus.PENDING,
    )

    estimated_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Free-text last-known location from the carrier feed.
    current_location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: When the tracking feed was last consulted — drives the refresh sweep.
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped[Order] = relationship(back_populates="shipments")
    tracking_events: Mapped[list[TrackingEvent]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TrackingEvent.occurred_at",
    )


class TrackingEvent(TenantScopedBase):
    """One carrier scan or status change for a shipment."""

    __tablename__ = "tracking_events"

    __table_args__ = (Index("ix_tracking_events_tenant_shipment", "tenant_id", "shipment_id"),)

    shipment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("shipments.id", ondelete="CASCADE"),
        nullable=False,
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    shipment: Mapped[Shipment] = relationship(back_populates="tracking_events")


class OrderEvent(TenantScopedBase):
    """A timeline entry for an order.

    The audit trail the timeline endpoint reads. Status values are stored as
    strings rather than the enum so a raw supplier status can appear in the
    timeline verbatim when it has no mapping — losing it would hide exactly the
    case worth investigating.
    """

    __tablename__ = "order_events"

    __table_args__ = (Index("ix_order_events_tenant_order", "tenant_id", "order_id"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    event_type: Mapped[OrderEventType] = mapped_column(
        _enum(OrderEventType, "order_event_type"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped[Order] = relationship(back_populates="events")


class OrderSyncRun(TenantScopedBase):
    """One synchronisation attempt, successful or not.

    The failures are the rows worth reading — "why are my orders stale" is
    asked long after the run, and a table recording only successes cannot
    answer it. Mirrors ``ProductImport``.
    """

    __tablename__ = "order_sync_runs"

    __table_args__ = (Index("ix_order_sync_runs_tenant_started", "tenant_id", "started_at"),)

    trigger: Mapped[SyncTrigger] = mapped_column(
        _enum(SyncTrigger, "order_sync_trigger"),
        nullable=False,
        default=SyncTrigger.MANUAL,
    )
    status: Mapped[SyncRunStatus] = mapped_column(
        _enum(SyncRunStatus, "order_sync_status"),
        nullable=False,
        default=SyncRunStatus.RUNNING,
    )

    #: The time window the run asked the supplier for.
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    orders_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "FULFILLMENT_TRANSITIONS",
    "FulfillmentStatus",
    "Order",
    "OrderEvent",
    "OrderEventType",
    "OrderItem",
    "OrderSource",
    "OrderSyncRun",
    "PaymentStatus",
    "Shipment",
    "ShipmentStatus",
    "SyncRunStatus",
    "SyncTrigger",
    "TrackingEvent",
    "can_transition",
]
