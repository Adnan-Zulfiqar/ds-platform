"""Index eBay buyer usernames on orders (review finding on EBAY-C5).

``EbayOrderBuyersOwner`` runs ``UPDATE orders ... WHERE source = 'ebay' AND
marketplace_buyer_username = ?`` for every eBay account-deletion notice, and
eBay sends those for eBay users platform-wide. Without an index each notice
scans every workspace's orders. A partial index covers exactly the rows the
eraser can match and nothing else. Index only; ``downgrade()`` drops it.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_INDEX = "ix_orders_ebay_buyer_username"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "orders",
        ["marketplace_buyer_username"],
        postgresql_where=sa.text("source = 'ebay' AND marketplace_buyer_username IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="orders")
