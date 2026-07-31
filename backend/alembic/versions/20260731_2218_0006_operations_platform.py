"""Phase 6 — operations platform domain.

Stores, inventory sync history, pricing rules and audit, automation rules and
runs, notifications, and daily analytics rollups. Also adds ``sell_price`` and
``store_id`` on products.

Spurious unique-constraint rewrites on ``refresh_tokens``, ``roles``, and
``tenants`` that autogenerate proposed are omitted — same reason as migrations
0004 and 0005.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create shared sync enums once. Tables below must use create_type=False
    # or PostgreSQL raises DuplicateObject on the second CREATE TYPE.
    sync_run_status = postgresql.ENUM(
        "running",
        "succeeded",
        "failed",
        "partial",
        name="sync_run_status",
        create_type=False,
    )
    sync_trigger = postgresql.ENUM(
        "manual",
        "scheduled",
        "webhook",
        name="sync_trigger",
        create_type=False,
    )
    bind = op.get_bind()
    postgresql.ENUM("running", "succeeded", "failed", "partial", name="sync_run_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM("manual", "scheduled", "webhook", name="sync_trigger").create(
        bind, checkfirst=True
    )

    op.create_table(
        "notifications",
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "import_completed",
                "sync_failed",
                "inventory_changed",
                "price_changed",
                "order_imported",
                "shipment_updated",
                "webhook_failure",
                "task_failure",
                "automation_completed",
                "automation_failed",
                "info",
                name="notification_kind",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("href", sa.String(length=512), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_notifications_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index(
        op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_notifications_deleted_at"), "notifications", ["deleted_at"], unique=False
    )
    op.create_index(
        "ix_notifications_tenant_created",
        "notifications",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_tenant_id"), "notifications", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_notifications_tenant_unread",
        "notifications",
        ["tenant_id", "is_read", "created_at"],
        unique=False,
    )
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)
    op.create_table(
        "stores",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "platform",
            sa.Enum(
                "shopify",
                "woocommerce",
                "ebay",
                "etsy",
                "tiktok_shop",
                "manual",
                name="store_platform",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "connected", "disconnected", "error", "syncing", name="store_status"
            ),
            nullable=False,
        ),
        sa.Column("storefront_url", sa.String(length=1024), nullable=True),
        sa.Column("external_store_id", sa.String(length=128), nullable=True),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inventory_sync_enabled", sa.Boolean(), nullable=False),
        sa.Column("pricing_sync_enabled", sa.Boolean(), nullable=False),
        sa.Column("order_sync_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1024), nullable=True),
        sa.Column("health_score", sa.Integer(), nullable=False),
        sa.Column("connected_by_user_id", sa.UUID(), nullable=True),
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
            ["connected_by_user_id"],
            ["users.id"],
            name=op.f("fk_stores_connected_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_stores_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stores")),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_stores_tenant_slug"),
    )
    op.create_index(op.f("ix_stores_created_at"), "stores", ["created_at"], unique=False)
    op.create_index(op.f("ix_stores_deleted_at"), "stores", ["deleted_at"], unique=False)
    op.create_index(op.f("ix_stores_tenant_id"), "stores", ["tenant_id"], unique=False)
    op.create_index("ix_stores_tenant_platform", "stores", ["tenant_id", "platform"], unique=False)
    op.create_index("ix_stores_tenant_status", "stores", ["tenant_id", "status"], unique=False)
    op.create_table(
        "analytics_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=True),
        sa.Column("revenue", sa.Numeric(precision=16, scale=4), nullable=False),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("product_count", sa.Integer(), nullable=False),
        sa.Column("inventory_units", sa.Integer(), nullable=False),
        sa.Column("sync_runs", sa.Integer(), nullable=False),
        sa.Column("sync_failures", sa.Integer(), nullable=False),
        sa.Column("automation_runs", sa.Integer(), nullable=False),
        sa.Column("automation_failures", sa.Integer(), nullable=False),
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
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_analytics_daily_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_analytics_daily_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analytics_daily")),
        sa.UniqueConstraint(
            "tenant_id", "day", "store_id", name="uq_analytics_daily_tenant_day_store"
        ),
    )
    op.create_index(
        op.f("ix_analytics_daily_created_at"), "analytics_daily", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_analytics_daily_deleted_at"), "analytics_daily", ["deleted_at"], unique=False
    )
    op.create_index(
        "ix_analytics_daily_tenant_day", "analytics_daily", ["tenant_id", "day"], unique=False
    )
    op.create_index(
        op.f("ix_analytics_daily_tenant_id"), "analytics_daily", ["tenant_id"], unique=False
    )
    op.create_table(
        "automation_rules",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "sync_inventory",
                "update_pricing",
                "refresh_orders",
                "archive_completed_orders",
                "retry_failed_jobs",
                "import_product",
                name="automation_action",
            ),
            nullable=False,
        ),
        sa.Column(
            "schedule",
            sa.Enum("manual", "hourly", "daily", "weekly", name="automation_schedule"),
            nullable=False,
        ),
        sa.Column("store_id", sa.UUID(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
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
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_automation_rules_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_automation_rules_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_rules")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_automation_rules_tenant_name"),
    )
    op.create_index(
        op.f("ix_automation_rules_created_at"), "automation_rules", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_automation_rules_deleted_at"), "automation_rules", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_automation_rules_store_id"), "automation_rules", ["store_id"], unique=False
    )
    op.create_index(
        "ix_automation_rules_tenant_active",
        "automation_rules",
        ["tenant_id", "is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_automation_rules_tenant_id"), "automation_rules", ["tenant_id"], unique=False
    )
    op.create_table(
        "automation_runs",
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("status", sync_run_status, nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            ["rule_id"],
            ["automation_rules.id"],
            name=op.f("fk_automation_runs_rule_id_automation_rules"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_automation_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_runs")),
    )
    op.create_index(
        op.f("ix_automation_runs_created_at"), "automation_runs", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_automation_runs_deleted_at"), "automation_runs", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_automation_runs_rule_id"), "automation_runs", ["rule_id"], unique=False
    )
    op.create_index(
        "ix_automation_runs_tenant_created",
        "automation_runs",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_automation_runs_tenant_id"), "automation_runs", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_automation_runs_tenant_rule", "automation_runs", ["tenant_id", "rule_id"], unique=False
    )
    op.create_table(
        "inventory_sync_runs",
        sa.Column("store_id", sa.UUID(), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("trigger", sync_trigger, nullable=False),
        sa.Column("status", sync_run_status, nullable=False),
        sa.Column("products_seen", sa.Integer(), nullable=False),
        sa.Column("products_changed", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by_user_id", sa.UUID(), nullable=True),
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
            name=op.f("fk_inventory_sync_runs_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name=op.f("fk_inventory_sync_runs_requested_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_inventory_sync_runs_store_id_stores"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_inventory_sync_runs_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_sync_runs")),
    )
    op.create_index(
        op.f("ix_inventory_sync_runs_created_at"),
        "inventory_sync_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_sync_runs_deleted_at"),
        "inventory_sync_runs",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_sync_runs_product_id"),
        "inventory_sync_runs",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_sync_runs_store_id"), "inventory_sync_runs", ["store_id"], unique=False
    )
    op.create_index(
        "ix_inventory_sync_runs_tenant_created",
        "inventory_sync_runs",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_sync_runs_tenant_id"), "inventory_sync_runs", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_inventory_sync_runs_tenant_status",
        "inventory_sync_runs",
        ["tenant_id", "status"],
        unique=False,
    )
    op.create_table(
        "pricing_rules",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "scope",
            sa.Enum("global", "store", "category", "product", name="pricing_scope"),
            nullable=False,
        ),
        sa.Column(
            "strategy",
            sa.Enum("percentage_markup", "fixed_markup", "tiered", name="pricing_strategy"),
            nullable=False,
        ),
        sa.Column("store_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.String(length=64), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("markup_percent", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("markup_fixed", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("min_profit", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("max_price", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("tiers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            name=op.f("fk_pricing_rules_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_pricing_rules_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_pricing_rules_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_rules")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_pricing_rules_tenant_name"),
    )
    op.create_index(
        op.f("ix_pricing_rules_created_at"), "pricing_rules", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_pricing_rules_deleted_at"), "pricing_rules", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_pricing_rules_product_id"), "pricing_rules", ["product_id"], unique=False
    )
    op.create_index(op.f("ix_pricing_rules_store_id"), "pricing_rules", ["store_id"], unique=False)
    op.create_index(
        op.f("ix_pricing_rules_tenant_id"), "pricing_rules", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_pricing_rules_tenant_scope",
        "pricing_rules",
        ["tenant_id", "scope", "priority"],
        unique=False,
    )
    op.create_table(
        "inventory_changes",
        sa.Column("sync_run_id", sa.UUID(), nullable=True),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=True),
        sa.Column("store_id", sa.UUID(), nullable=True),
        sa.Column("previous_quantity", sa.Integer(), nullable=False),
        sa.Column("new_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "supplier_sync",
                "manual",
                "order_reserved",
                "order_released",
                "import",
                name="inventory_change_reason",
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
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
            name=op.f("fk_inventory_changes_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_inventory_changes_store_id_stores"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sync_run_id"],
            ["inventory_sync_runs.id"],
            name=op.f("fk_inventory_changes_sync_run_id_inventory_sync_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_inventory_changes_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_inventory_changes_variant_id_product_variants"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_changes")),
    )
    op.create_index(
        op.f("ix_inventory_changes_created_at"), "inventory_changes", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_changes_deleted_at"), "inventory_changes", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_changes_product_id"), "inventory_changes", ["product_id"], unique=False
    )
    op.create_index(
        op.f("ix_inventory_changes_sync_run_id"), "inventory_changes", ["sync_run_id"], unique=False
    )
    op.create_index(
        "ix_inventory_changes_tenant_created",
        "inventory_changes",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_changes_tenant_id"), "inventory_changes", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_inventory_changes_tenant_product",
        "inventory_changes",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.create_table(
        "price_changes",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=True),
        sa.Column("rule_id", sa.UUID(), nullable=True),
        sa.Column("store_id", sa.UUID(), nullable=True),
        sa.Column("previous_price", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("new_price", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("cost_price", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("applied_by_user_id", sa.UUID(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
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
            ["applied_by_user_id"],
            ["users.id"],
            name=op.f("fk_price_changes_applied_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_price_changes_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["pricing_rules.id"],
            name=op.f("fk_price_changes_rule_id_pricing_rules"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_price_changes_store_id_stores"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_price_changes_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["product_variants.id"],
            name=op.f("fk_price_changes_variant_id_product_variants"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_changes")),
    )
    op.create_index(
        op.f("ix_price_changes_created_at"), "price_changes", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_price_changes_deleted_at"), "price_changes", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_price_changes_product_id"), "price_changes", ["product_id"], unique=False
    )
    op.create_index(
        "ix_price_changes_tenant_created",
        "price_changes",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_price_changes_tenant_id"), "price_changes", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_price_changes_tenant_product",
        "price_changes",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.add_column(
        "products", sa.Column("sell_price", sa.Numeric(precision=16, scale=4), nullable=True)
    )
    op.add_column("products", sa.Column("store_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_products_store_id"), "products", ["store_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_products_store_id_stores"),
        "products",
        "stores",
        ["store_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_products_store_id_stores"), "products", type_="foreignkey")
    op.drop_index(op.f("ix_products_store_id"), table_name="products")
    op.drop_column("products", "store_id")
    op.drop_column("products", "sell_price")
    op.drop_index("ix_price_changes_tenant_product", table_name="price_changes")
    op.drop_index(op.f("ix_price_changes_tenant_id"), table_name="price_changes")
    op.drop_index("ix_price_changes_tenant_created", table_name="price_changes")
    op.drop_index(op.f("ix_price_changes_product_id"), table_name="price_changes")
    op.drop_index(op.f("ix_price_changes_deleted_at"), table_name="price_changes")
    op.drop_index(op.f("ix_price_changes_created_at"), table_name="price_changes")
    op.drop_table("price_changes")
    op.drop_index("ix_inventory_changes_tenant_product", table_name="inventory_changes")
    op.drop_index(op.f("ix_inventory_changes_tenant_id"), table_name="inventory_changes")
    op.drop_index("ix_inventory_changes_tenant_created", table_name="inventory_changes")
    op.drop_index(op.f("ix_inventory_changes_sync_run_id"), table_name="inventory_changes")
    op.drop_index(op.f("ix_inventory_changes_product_id"), table_name="inventory_changes")
    op.drop_index(op.f("ix_inventory_changes_deleted_at"), table_name="inventory_changes")
    op.drop_index(op.f("ix_inventory_changes_created_at"), table_name="inventory_changes")
    op.drop_table("inventory_changes")
    op.drop_index("ix_pricing_rules_tenant_scope", table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_tenant_id"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_store_id"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_product_id"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_deleted_at"), table_name="pricing_rules")
    op.drop_index(op.f("ix_pricing_rules_created_at"), table_name="pricing_rules")
    op.drop_table("pricing_rules")
    op.drop_index("ix_inventory_sync_runs_tenant_status", table_name="inventory_sync_runs")
    op.drop_index(op.f("ix_inventory_sync_runs_tenant_id"), table_name="inventory_sync_runs")
    op.drop_index("ix_inventory_sync_runs_tenant_created", table_name="inventory_sync_runs")
    op.drop_index(op.f("ix_inventory_sync_runs_store_id"), table_name="inventory_sync_runs")
    op.drop_index(op.f("ix_inventory_sync_runs_product_id"), table_name="inventory_sync_runs")
    op.drop_index(op.f("ix_inventory_sync_runs_deleted_at"), table_name="inventory_sync_runs")
    op.drop_index(op.f("ix_inventory_sync_runs_created_at"), table_name="inventory_sync_runs")
    op.drop_table("inventory_sync_runs")
    op.drop_index("ix_automation_runs_tenant_rule", table_name="automation_runs")
    op.drop_index(op.f("ix_automation_runs_tenant_id"), table_name="automation_runs")
    op.drop_index("ix_automation_runs_tenant_created", table_name="automation_runs")
    op.drop_index(op.f("ix_automation_runs_rule_id"), table_name="automation_runs")
    op.drop_index(op.f("ix_automation_runs_deleted_at"), table_name="automation_runs")
    op.drop_index(op.f("ix_automation_runs_created_at"), table_name="automation_runs")
    op.drop_table("automation_runs")
    op.drop_index(op.f("ix_automation_rules_tenant_id"), table_name="automation_rules")
    op.drop_index("ix_automation_rules_tenant_active", table_name="automation_rules")
    op.drop_index(op.f("ix_automation_rules_store_id"), table_name="automation_rules")
    op.drop_index(op.f("ix_automation_rules_deleted_at"), table_name="automation_rules")
    op.drop_index(op.f("ix_automation_rules_created_at"), table_name="automation_rules")
    op.drop_table("automation_rules")
    op.drop_index(op.f("ix_analytics_daily_tenant_id"), table_name="analytics_daily")
    op.drop_index("ix_analytics_daily_tenant_day", table_name="analytics_daily")
    op.drop_index(op.f("ix_analytics_daily_deleted_at"), table_name="analytics_daily")
    op.drop_index(op.f("ix_analytics_daily_created_at"), table_name="analytics_daily")
    op.drop_table("analytics_daily")
    op.drop_index("ix_stores_tenant_status", table_name="stores")
    op.drop_index("ix_stores_tenant_platform", table_name="stores")
    op.drop_index(op.f("ix_stores_tenant_id"), table_name="stores")
    op.drop_index(op.f("ix_stores_deleted_at"), table_name="stores")
    op.drop_index(op.f("ix_stores_created_at"), table_name="stores")
    op.drop_table("stores")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index("ix_notifications_tenant_unread", table_name="notifications")
    op.drop_index(op.f("ix_notifications_tenant_id"), table_name="notifications")
    op.drop_index("ix_notifications_tenant_created", table_name="notifications")
    op.drop_index(op.f("ix_notifications_deleted_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_table("notifications")

    # Autogenerate omits DROP TYPE for native enums; without these a later
    # upgrade head fails with "type already exists".
    bind = op.get_bind()
    for name in (
        "notification_kind",
        "store_platform",
        "store_status",
        "automation_action",
        "automation_schedule",
        "pricing_scope",
        "pricing_strategy",
        "inventory_change_reason",
        "sync_run_status",
        "sync_trigger",
    ):
        sa.Enum(name=name).drop(bind, checkfirst=True)
