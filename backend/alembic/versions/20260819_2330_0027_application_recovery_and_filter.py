"""Crash recovery and durable filter selection for rule applications (M3A-4B).

Additive. 0023-0026 are not modified.

* ``heartbeat_at`` — the worker stamps this at every batch boundary. A run
  left ``running`` by a crashed worker is otherwise indistinguishable from one
  that is simply slow, so nothing could safely reclaim it. With a heartbeat,
  "stale" is a measurable fact rather than a guess, and the reconciler can act
  without ever stealing a run that is still making progress.
* ``recovery_count`` — how many times a run has been reclaimed. Bounded, so a
  run that fails the same way forever is eventually parked as ``failed``
  instead of being resumed in a loop, and the number is visible in the audit
  trail rather than inferred from logs.
* ``selection_filter`` — the filter a merchant confirmed, when they chose
  "everything matching" rather than a list. Stored because the worker receives
  only an id: without it, selecting 5,000 drafts would mean the browser
  enumerating and posting 5,000 identifiers, and the record would no longer
  explain itself once the catalogue moved on.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_applications",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "rule_applications",
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rule_applications",
        sa.Column("selection_filter", postgresql.JSONB(), nullable=True),
    )

    # The reconciler's only query: running rows ordered by how long they have
    # been silent. Without the index it is a full scan of every application
    # ever recorded, on a schedule.
    op.create_index(
        "ix_rule_applications_running_heartbeat",
        "rule_applications",
        ["heartbeat_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_rule_applications_running_heartbeat", table_name="rule_applications")
    for column in ("selection_filter", "recovery_count", "heartbeat_at"):
        op.drop_column("rule_applications", column)
