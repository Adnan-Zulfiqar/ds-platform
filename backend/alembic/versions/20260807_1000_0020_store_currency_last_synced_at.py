"""Add currency_last_synced_at so Store.currency is only trusted after Shopify sync.

Revision ID: 0020
Revises: 0019
Created: 2026-08-07 10:00:00+00:00

Existing rows keep currency_last_synced_at = NULL. A legacy default of USD with
a null sync timestamp must NOT be treated as verified Shopify selling currency.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stores",
        sa.Column("currency_last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stores", "currency_last_synced_at")
