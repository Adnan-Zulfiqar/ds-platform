"""Pricing rules: a sale fee as a share of the selling price (Track E2, M24C).

``pricing_rules.sale_fee_percent`` — nullable; marketplace final-value and
payment-processing fees that grow with the price. Null on every existing
rule, which keeps every existing price unchanged. ``downgrade()`` drops it.

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pricing_rules",
        sa.Column("sale_fee_percent", sa.Numeric(8, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pricing_rules", "sale_fee_percent")
