"""Supplier orders: the AliExpress order behind a customer's channel order
(Track F, decision D-017).

One row per channel order (Shopify, eBay, WooCommerce). It records whether
the order could be placed, the AliExpress order id(s) it produced, and the
tracking number that later came back. It never holds the buyer's address:
that stays on ``orders`` and is read at the moment of placing.

**The state machine is what prevents a second supplier order.**

    needs_review ──(fixed, retried)──► queued ──► placing ──► placed ──► shipped
                                         ▲           │
                                         └─ failed ◄─┘

``placing`` is committed *before* AliExpress is called. If the worker dies
between the call and recording its answer, the row stays ``placing`` and is
never retried automatically: the merchant checks AliExpress first, because
retrying blind could buy the goods twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase


class SupplierOrderStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    QUEUED = "queued"
    PLACING = "placing"
    PLACED = "placed"
    FAILED = "failed"
    SHIPPED = "shipped"


class SupplierOrder(TenantScopedBase):
    __tablename__ = "supplier_orders"

    __table_args__ = (
        UniqueConstraint("tenant_id", "order_id", name="uq_supplier_orders_tenant_order"),
        Index("ix_supplier_orders_tenant_status", "tenant_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    #: A ``SupplierOrderStatus`` value; a string column, as billing's status.
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    #: ``manual`` (the button) or ``auto`` (the workspace switch).
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    #: Why the order cannot be placed as it stands, as stable codes.
    review_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: What was sent: product id, sku_attr and quantity per line. No address.
    request_lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: AliExpress order ids. Several when the lines come from several sellers.
    external_order_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tracking_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracking_carrier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: When the tracking number was sent to the sales channel.
    tracking_pushed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FulfilmentSettings(TenantScopedBase):
    """The workspace's two switches (D-017). Both off until the owner turns
    them on; a missing row means "off"."""

    __tablename__ = "fulfilment_settings"

    __table_args__ = (UniqueConstraint("tenant_id", name="uq_fulfilment_settings_tenant"),)

    #: Place paid, cleanly mapped orders on AliExpress without the button.
    auto_order: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Send AliExpress tracking numbers to the sales channel automatically.
    auto_tracking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: Used only if AliExpress refuses the supplier's default method.
    fallback_shipping_method: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: When *Auto-order* was last switched on. Auto mode only takes orders
    #: placed after this, so switching it on never buys orders the merchant
    #: already handled another way.
    auto_order_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["FulfilmentSettings", "SupplierOrder", "SupplierOrderStatus"]
