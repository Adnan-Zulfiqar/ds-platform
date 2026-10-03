"""Product category and item specifics per marketplace (EBAY-C3, D-C3-3).

New tenant-scoped table ``product_marketplace_attributes``: one row per
product and marketplace holding the chosen eBay category and the aspects
(item specifics) eBay requires for it. Product data, not seller-account
data, so not part of eBay's deletion contract. A new table only;
``downgrade()`` drops it.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

_TABLE = "product_marketplace_attributes"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("category_id", sa.String(16), nullable=True),
        sa.Column("category_name", sa.String(255), nullable=True),
        sa.Column(
            "aspects",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tenant_id",
            "product_id",
            "marketplace_id",
            name="uq_product_marketplace_attributes_tenant_product_marketplace",
        ),
    )
    for column in ("tenant_id", "created_at", "deleted_at", "product_id"):
        op.create_index(f"ix_{_TABLE}_{column}", _TABLE, [column])


def downgrade() -> None:
    for column in ("product_id", "deleted_at", "created_at", "tenant_id"):
        op.drop_index(f"ix_{_TABLE}_{column}", table_name=_TABLE)
    op.drop_table(_TABLE)
