"""eBay orders (EBAY-C5).

* ``order_source`` gains ``'ebay'``.
* ``orders.marketplace_buyer_username`` — nullable; the eBay buyer's
  username, kept only so an eBay account-deletion notice for that buyer can
  find and anonymise the order. Null on every existing row.

Downgrade drops the column. The enum value stays, as migration 0008 did for
``'shopify'``: PostgreSQL cannot drop an enum value without rebuilding the
type, and a value nothing references is harmless.

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE order_source ADD VALUE IF NOT EXISTS 'ebay'")
    op.add_column(
        "orders",
        sa.Column("marketplace_buyer_username", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "marketplace_buyer_username")
