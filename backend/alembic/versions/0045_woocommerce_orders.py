"""WooCommerce orders (Track E7 W4).

* ``order_source`` gains ``'woocommerce'``.

Downgrade is a no-op: as in 0008 ('shopify') and 0040 ('ebay'), PostgreSQL
cannot drop an enum value without rebuilding the type, and a value nothing
references is harmless. Rows imported with it must be removed first if the
code is rolled back.

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE order_source ADD VALUE IF NOT EXISTS 'woocommerce'")


def downgrade() -> None:
    """Intentionally empty; see the module docstring."""
