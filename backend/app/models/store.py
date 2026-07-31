"""Sales-channel stores.

A store is where a tenant *sells* — Shopify, WooCommerce, and siblings — as
distinct from a *supplier* connection (AliExpress). Credentials, when present,
are encrypted at rest the same way AliExpress tokens are; the API never returns
them, even masked.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase


class StorePlatform(StrEnum):
    """Sales channels the platform targets.

    AliExpress is intentionally absent: it is a supplier integration with its
    own table, not a sales-channel store. Conflating the two would force one
    credential shape onto two different marketplaces.
    """

    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"
    EBAY = "ebay"
    ETSY = "etsy"
    TIKTOK_SHOP = "tiktok_shop"
    MANUAL = "manual"


class StoreStatus(StrEnum):
    """Lifecycle of a store connection."""

    PENDING = "pending"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    SYNCING = "syncing"


class Store(TenantScopedBase):
    """A tenant's sales-channel store."""

    __tablename__ = "stores"

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_stores_tenant_slug"),
        Index("ix_stores_tenant_status", "tenant_id", "status"),
        Index("ix_stores_tenant_platform", "tenant_id", "platform"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[StorePlatform] = mapped_column(
        Enum(
            StorePlatform,
            name="store_platform",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=StorePlatform.MANUAL,
    )
    status: Mapped[StoreStatus] = mapped_column(
        Enum(
            StoreStatus,
            name="store_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=StoreStatus.PENDING,
    )

    #: Public storefront URL when known — not a secret.
    storefront_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    external_store_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: Ciphertext of platform credentials. Never returned by the API.
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    #: Per-store automation and pricing switches. Shape is owned by services;
    #: the column is a bag so adding a toggle does not need a migration.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    inventory_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pricing_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    order_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = ["Store", "StorePlatform", "StoreStatus"]
