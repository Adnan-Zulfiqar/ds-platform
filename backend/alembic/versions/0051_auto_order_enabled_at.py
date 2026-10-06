"""When the workspace switched auto-order on (Track F, review finding C3).

One nullable column, ``fulfilment_settings.auto_order_enabled_at``. Auto mode
only places orders created after it, so switching the mode on cannot buy
orders the merchant already fulfilled another way. Existing rows get NULL,
which auto mode reads as "not enabled since the fix": a workspace that had
switched it on must switch it on again. ``downgrade()`` drops the column.

Revision ID: 0051
Revises: 0050
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fulfilment_settings",
        sa.Column("auto_order_enabled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fulfilment_settings", "auto_order_enabled_at")
