"""Inventory synchronisation history.

Stock itself lives on ``Product`` / ``ProductVariant``. These tables record
*what changed and when*, so an operator can answer "why did this SKU jump from
40 to 0" without reading application logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase
from app.models.order import SyncRunStatus, SyncTrigger


class InventoryChangeReason(StrEnum):
    """Why a stock figure moved."""

    SUPPLIER_SYNC = "supplier_sync"
    MANUAL = "manual"
    ORDER_RESERVED = "order_reserved"
    ORDER_RELEASED = "order_released"
    IMPORT = "import"


class InventorySyncRun(TenantScopedBase):
    """One inventory synchronisation attempt."""

    __tablename__ = "inventory_sync_runs"

    __table_args__ = (
        Index("ix_inventory_sync_runs_tenant_created", "tenant_id", "created_at"),
        Index("ix_inventory_sync_runs_tenant_status", "tenant_id", "status"),
    )

    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    trigger: Mapped[SyncTrigger] = mapped_column(
        Enum(
            SyncTrigger,
            name="sync_trigger",
            values_callable=lambda enum: [member.value for member in enum],
            create_constraint=False,
            create_type=False,
        ),
        nullable=False,
        default=SyncTrigger.MANUAL,
    )
    status: Mapped[SyncRunStatus] = mapped_column(
        Enum(
            SyncRunStatus,
            name="sync_run_status",
            values_callable=lambda enum: [member.value for member in enum],
            create_constraint=False,
            create_type=False,
        ),
        nullable=False,
        default=SyncRunStatus.RUNNING,
    )
    products_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    products_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class InventoryChange(TenantScopedBase):
    """A single stock delta for a product (and optional variant)."""

    __tablename__ = "inventory_changes"

    __table_args__ = (
        Index("ix_inventory_changes_tenant_product", "tenant_id", "product_id"),
        Index("ix_inventory_changes_tenant_created", "tenant_id", "created_at"),
    )

    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inventory_sync_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="SET NULL"),
        nullable=True,
    )
    previous_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    new_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[InventoryChangeReason] = mapped_column(
        Enum(
            InventoryChangeReason,
            name="inventory_change_reason",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=InventoryChangeReason.SUPPLIER_SYNC,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "InventoryChange",
    "InventoryChangeReason",
    "InventorySyncRun",
]
