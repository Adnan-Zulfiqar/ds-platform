"""Draft editor Stage 4 — media alt text and merchant variant pricing fields.

Revision ID: 0016
Revises: 0015
Created: 2026-08-06 19:45:00+00:00

Additive only. Supplier sync must not overwrite merchant_sku / sell_price /
compare_at_price / is_enabled, or merchant image alt_text / position.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_images",
        sa.Column("alt_text", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "product_images",
        sa.Column(
            "is_supplier",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    op.add_column(
        "product_variants",
        sa.Column("merchant_sku", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "product_variants",
        sa.Column("sell_price", sa.Numeric(precision=16, scale=4), nullable=True),
    )
    op.add_column(
        "product_variants",
        sa.Column("compare_at_price", sa.Numeric(precision=16, scale=4), nullable=True),
    )
    op.add_column(
        "product_variants",
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("product_variants", "is_enabled")
    op.drop_column("product_variants", "compare_at_price")
    op.drop_column("product_variants", "sell_price")
    op.drop_column("product_variants", "merchant_sku")
    op.drop_column("product_images", "is_supplier")
    op.drop_column("product_images", "alt_text")
