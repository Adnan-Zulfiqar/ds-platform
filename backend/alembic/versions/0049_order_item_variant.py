"""Order lines remember the exact catalogue variant (supplier auto-ordering).

One nullable column, ``order_items.variant_id`` (FK ``product_variants``,
SET NULL so deleting a variant never deletes order history), plus an index
leading with ``tenant_id``. Placing a supplier order needs the exact
AliExpress SKU per line; a product link alone is ambiguous for a product
with several variants. ``downgrade()`` drops the index and the column.

Revision ID: 0049
Revises: 0048
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_order_items_variant_id_product_variants",
        "order_items",
        "product_variants",
        ["variant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_order_items_tenant_variant", "order_items", ["tenant_id", "variant_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_tenant_variant", table_name="order_items")
    op.drop_constraint(
        "fk_order_items_variant_id_product_variants", "order_items", type_="foreignkey"
    )
    op.drop_column("order_items", "variant_id")
