"""Record AliExpress import ship-to destination on jobs and products.

Revision ID: 0019
Revises: 0018
Created: 2026-08-06 22:00:00+00:00

One product row per tenant+supplier id remains the model — destination is a
snapshot on the product and each import attempt, not a duplicate catalogue
row per country (see docs/ALIEXPRESS_INTEGRATION.md).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_imports",
        sa.Column("ship_to_country", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "product_imports",
        sa.Column("currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "product_imports",
        sa.Column("result_category", sa.String(length=64), nullable=True),
    )

    op.add_column(
        "products",
        sa.Column("import_ship_to_country", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("import_ship_to_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "import_ship_to_checked_at")
    op.drop_column("products", "import_ship_to_country")
    op.drop_column("product_imports", "result_category")
    op.drop_column("product_imports", "currency")
    op.drop_column("product_imports", "ship_to_country")
