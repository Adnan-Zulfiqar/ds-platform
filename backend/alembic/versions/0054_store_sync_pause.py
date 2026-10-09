"""Operator pause on a store (Admin Control Center phase 5, D-019).

Two nullable columns on ``stores``: when ``sync_paused_at`` is set,
DropPilot stops writing to that store (price and stock pushes, new
publishes) until an operator clears it. Existing rows are unpaused.
``downgrade()`` drops both columns.

Revision ID: 0054
Revises: 0053
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("sync_paused_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stores", sa.Column("sync_paused_reason", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("stores", "sync_paused_reason")
    op.drop_column("stores", "sync_paused_at")
