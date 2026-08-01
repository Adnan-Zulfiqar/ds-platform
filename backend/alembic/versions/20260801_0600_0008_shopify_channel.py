"""Phase 8 — Shopify channel connection, listings, order store_id.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enum value additions are not transactional on PostgreSQL — run outside
    # a transaction block when needed. Alembic's default transaction is fine
    # for ADD VALUE IF NOT EXISTS on PG 9.1+ with concurrent-safe form.
    op.execute("ALTER TYPE order_source ADD VALUE IF NOT EXISTS 'shopify'")
    op.execute("ALTER TYPE product_source ADD VALUE IF NOT EXISTS 'shopify'")

    listing_status = postgresql.ENUM(
        "pending",
        "synced",
        "error",
        "removed",
        name="listing_sync_status",
        create_type=True,
    )
    listing_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "shopify_connections",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("shop_domain", sa.String(length=255), nullable=False),
        sa.Column("shop_id", sa.String(length=64), nullable=True),
        sa.Column("encrypted_access_token", sa.String(length=2048), nullable=False),
        sa.Column("scopes", sa.String(length=1024), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "connected",
                "expired",
                "error",
                name="integration_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("webhooks_registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_shopify_connections_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_shopify_connections_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_shopify_connections_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shopify_connections")),
        sa.UniqueConstraint(
            "tenant_id",
            "shop_domain",
            name="uq_shopify_connections_tenant_shop",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "store_id",
            name="uq_shopify_connections_tenant_store",
        ),
    )
    op.create_index(
        op.f("ix_shopify_connections_tenant_id"),
        "shopify_connections",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_shopify_connections_tenant_status",
        "shopify_connections",
        ["tenant_id", "status"],
        unique=False,
    )

    op.create_table(
        "store_listings",
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("external_product_id", sa.String(length=128), nullable=False),
        sa.Column(
            "external_variant_map",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "inventory_item_map",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "synced",
                "error",
                "removed",
                name="listing_sync_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_store_listings_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_store_listings_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_store_listings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store_listings")),
        sa.UniqueConstraint(
            "tenant_id",
            "store_id",
            "product_id",
            name="uq_store_listings_tenant_store_product",
        ),
    )
    op.create_index(
        op.f("ix_store_listings_created_at"), "store_listings", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_store_listings_deleted_at"), "store_listings", ["deleted_at"], unique=False
    )
    op.create_index(
        "ix_store_listings_external_product",
        "store_listings",
        ["tenant_id", "external_product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_store_listings_tenant_id"), "store_listings", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_store_listings_tenant_store",
        "store_listings",
        ["tenant_id", "store_id"],
        unique=False,
    )

    op.add_column("orders", sa.Column("store_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_orders_store_id"), "orders", ["store_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_orders_store_id_stores"),
        "orders",
        "stores",
        ["store_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_orders_store_id_stores"), "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_store_id"), table_name="orders")
    op.drop_column("orders", "store_id")

    op.drop_index("ix_store_listings_tenant_store", table_name="store_listings")
    op.drop_index(op.f("ix_store_listings_tenant_id"), table_name="store_listings")
    op.drop_index("ix_store_listings_external_product", table_name="store_listings")
    op.drop_index(op.f("ix_store_listings_deleted_at"), table_name="store_listings")
    op.drop_index(op.f("ix_store_listings_created_at"), table_name="store_listings")
    op.drop_table("store_listings")

    op.drop_index("ix_shopify_connections_tenant_status", table_name="shopify_connections")
    op.drop_index(op.f("ix_shopify_connections_tenant_id"), table_name="shopify_connections")
    op.drop_table("shopify_connections")

    op.execute("DROP TYPE IF EXISTS listing_sync_status")
    # PostgreSQL cannot remove enum values safely — leave shopify on sources.
