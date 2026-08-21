"""Queue state for background rule application (M3A-3 acceptance fix).

Three columns on ``rule_applications``, all additive. 0023, 0024 and 0025 are
not touched.

Execution moved off the request thread onto the existing Celery queue, which
introduces three facts the row must carry durably:

* ``claimed_by_task_id`` — which Celery task owns this run. The claim itself
  is the ``pending -> running`` status transition, but a *retry* of the same
  task arrives to find the run already ``running`` and must be able to tell
  "this is my own redelivery, resume it" from "another worker holds this,
  leave it alone". Only the task id distinguishes those.
* ``processed_count`` — how far through the durable selection the run got.
  A cursor rather than a derived count: an item whose product id no longer
  resolves is recorded with a NULL ``product_id``, so progress cannot be
  recovered by comparing recorded rows against the selection. The cursor and
  the items it accounts for commit in the same transaction, which is what
  makes a resumed batch neither skip nor repeat work.
* ``enqueued_at`` — when the broker accepted the message. A run that is
  ``pending`` with this still NULL was never queued, which is the difference
  between "waiting for a worker" and "nothing is ever going to happen".

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_applications",
        sa.Column("claimed_by_task_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "rule_applications",
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rule_applications",
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    for column in ("enqueued_at", "processed_count", "claimed_by_task_id"):
        op.drop_column("rule_applications", column)
