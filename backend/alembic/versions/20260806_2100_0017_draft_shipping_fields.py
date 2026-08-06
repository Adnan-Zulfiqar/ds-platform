"""Draft editor Stage 5 — persist supplier package/shipping snapshot.

Revision ID: 0017
Revises: 0016
Created: 2026-08-06 21:00:00+00:00

AliExpress already parses package/logistics wire fields; this stores them so
the Shipping workspace can show weight, dimensions, and delivery estimates.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("package_weight_kg", sa.Numeric(precision=12, scale=4), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("package_length_cm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("package_width_cm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("package_height_cm", sa.Integer(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("delivery_time_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("ship_to_country", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("shipping_cost", sa.Numeric(precision=16, scale=4), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("warehouse_origin", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "warehouse_origin")
    op.drop_column("products", "shipping_cost")
    op.drop_column("products", "ship_to_country")
    op.drop_column("products", "delivery_time_days")
    op.drop_column("products", "package_height_cm")
    op.drop_column("products", "package_width_cm")
    op.drop_column("products", "package_length_cm")
    op.drop_column("products", "package_weight_kg")
