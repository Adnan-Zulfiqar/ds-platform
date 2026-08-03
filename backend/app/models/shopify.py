"""Shopify sales-channel connection and product listings.

Shopify is a *sales channel*, not a supplier. Tokens live here (encrypted);
catalogue products keep their supplier ``external_id``. Channel product IDs
are stored on :class:`StoreListing` so a product can be published to many shops
without overloading the supplier identity columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase, TenantScopedBase
from app.models.integration import IntegrationStatus


class ListingSyncStatus(StrEnum):
    """Channel listing lifecycle."""

    PENDING = "pending"
    SYNCED = "synced"
    ERROR = "error"
    REMOVED = "removed"


class ShopifyConnection(IdentifiedBase):
    """OAuth connection for one Shopify shop, bound to a :class:`Store`.

    Hard-deleted on disconnect (same rationale as AliExpressConnection): the
    customer asked us to forget their store credentials.
    """

    __tablename__ = "shopify_connections"

    __table_args__ = (
        UniqueConstraint("tenant_id", "shop_domain", name="uq_shopify_connections_tenant_shop"),
        UniqueConstraint("tenant_id", "store_id", name="uq_shopify_connections_tenant_store"),
        # Global: one Shopify shop belongs to at most one DropPilot tenant.
        # Without this, webhook resolution by domain can write into the wrong
        # workspace (audit A-01).
        UniqueConstraint("shop_domain", name="uq_shopify_connections_shop_domain"),
        Index("ix_shopify_connections_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: e.g. ``mystore.myshopify.com`` — public identifier, not a secret.
    shop_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    shop_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    encrypted_access_token: Mapped[str] = mapped_column(String(2048), nullable=False)
    scopes: Mapped[str] = mapped_column(String(1024), nullable=False, default="")

    status: Mapped[IntegrationStatus] = mapped_column(
        Enum(
            IntegrationStatus,
            name="integration_status",
            create_constraint=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=IntegrationStatus.PENDING,
    )

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webhooks_registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StoreListing(TenantScopedBase):
    """Maps a DropPilot product to a channel product on a store."""

    __tablename__ = "store_listings"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "store_id",
            "product_id",
            name="uq_store_listings_tenant_store_product",
        ),
        Index("ix_store_listings_tenant_store", "tenant_id", "store_id"),
        Index("ix_store_listings_external_product", "tenant_id", "external_product_id"),
    )

    store_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )

    external_product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: DropPilot variant id (str) → Shopify variant id.
    external_variant_map: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    #: DropPilot variant id (str) → Shopify inventory_item_id.
    inventory_item_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[ListingSyncStatus] = mapped_column(
        Enum(
            ListingSyncStatus,
            name="listing_sync_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ListingSyncStatus.PENDING,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "ListingSyncStatus",
    "ShopifyConnection",
    "StoreListing",
]
