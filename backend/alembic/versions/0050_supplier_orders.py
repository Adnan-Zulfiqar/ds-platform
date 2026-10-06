"""Supplier orders and the workspace fulfilment switches (Track F, D-017).

Two new tenant-scoped tables, ``supplier_orders`` and ``fulfilment_settings``.
No existing table changes. ``downgrade()`` drops both.

Revision ID: 0050
Revises: 0049
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def _base_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
    ]


#: The columns ``TenantScopedBase`` indexes, as 0047 creates them.
_BASE_INDEXED = ("tenant_id", "created_at", "deleted_at")


def _base_indexes(table: str) -> None:
    for column in _BASE_INDEXED:
        op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "supplier_orders",
        *_base_columns(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
        sa.Column(
            "review_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "request_lines",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "external_order_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.String(1024), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tracking_number", sa.String(128), nullable=True),
        sa.Column("tracking_carrier", sa.String(128), nullable=True),
        sa.Column("tracking_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("tenant_id", "order_id", name="uq_supplier_orders_tenant_order"),
    )
    op.create_index("ix_supplier_orders_tenant_status", "supplier_orders", ["tenant_id", "status"])
    _base_indexes("supplier_orders")

    op.create_table(
        "fulfilment_settings",
        *_base_columns(),
        sa.Column("auto_order", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("auto_tracking", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fallback_shipping_method", sa.String(128), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", name="uq_fulfilment_settings_tenant"),
    )
    _base_indexes("fulfilment_settings")


def downgrade() -> None:
    for table in ("fulfilment_settings", "supplier_orders"):
        for column in _BASE_INDEXED:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
    op.drop_index("ix_supplier_orders_tenant_status", table_name="supplier_orders")
    op.drop_table("fulfilment_settings")
    op.drop_table("supplier_orders")
