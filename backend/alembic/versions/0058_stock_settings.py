"""The workspace's listed-stock setting (owner request 2026-10-12).

One new tenant-scoped table, ``stock_settings``: one row per workspace
holding the quantity every sales channel lists, or NULL for "the supplier's
stock". No existing table changes. ``downgrade()`` drops it.

Revision ID: 0058
Revises: 0057
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None

#: The columns ``TenantScopedBase`` indexes, as 0047 creates them.
_BASE_INDEXED = ("tenant_id", "created_at", "deleted_at")


def upgrade() -> None:
    op.create_table(
        "stock_settings",
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
        sa.Column("publish_quantity", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", name="uq_stock_settings_tenant"),
        sa.CheckConstraint(
            "publish_quantity IS NULL OR publish_quantity BETWEEN 1 AND 10000",
            name="ck_stock_settings_publish_quantity_range",
        ),
    )
    for column in _BASE_INDEXED:
        op.create_index(f"ix_stock_settings_{column}", "stock_settings", [column])


def downgrade() -> None:
    for column in _BASE_INDEXED:
        op.drop_index(f"ix_stock_settings_{column}", table_name="stock_settings")
    op.drop_table("stock_settings")
