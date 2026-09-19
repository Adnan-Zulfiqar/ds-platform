"""Nullable JSONB analysis evidence on product_images (Phase 9 Stage 6).

Additive. No back-fill and no server default: existing rows have no analysis,
and inventing an empty object would look like a completed run.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_images",
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_images", "analysis")
