"""Durable ownership lease for rule applications (M3A acceptance fix).

Additive. 0023-0027 are not modified.

* ``lease_token`` — the fence. ``claimed_by_task_id`` alone cannot be one: a
  Celery task id is reused across every *retry* of the same message, so two
  processes handed the same message hold the same identifier, and a check
  against it would let both write. A token minted fresh on every successful
  claim, re-claim and re-lease is unique per *attempt*, which is the property
  a fence needs. The worker carries the token it was issued and every batch is
  conditional on it still being the one on the row, so a worker whose run was
  reclaimed is rejected before it can write a price.

* The heartbeat backfill — ``heartbeat_at`` arrived in 0027, so any run left
  ``running`` by a worker that died *before* that migration has ``NULL`` there
  and is invisible to ``heartbeat_at < cutoff``. Those rows are unrecoverable
  by the reconciler and would sit ``running`` forever. Backfilled from the best
  evidence the row already carries of when it was last known to be alive.
  Only ``running`` rows are touched, and only where the column is ``NULL``, so
  nothing that already has a heartbeat is disturbed.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_applications",
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Legacy `running` rows predating 0027. `started_at` is when a worker took
    # the run, which is the closest thing to a last-seen-alive that these rows
    # have; `updated_at` and `created_at` are the fallbacks. The reconciler
    # requires the resulting timestamp to be older than STALE_AFTER before it
    # will act, so this makes them *eligible* for recovery rather than
    # immediately reclaimed.
    op.execute(
        sa.text(
            """
            UPDATE rule_applications
               SET heartbeat_at = COALESCE(started_at, updated_at, created_at)
             WHERE status = 'running'
               AND heartbeat_at IS NULL
            """
        )
    )


def downgrade() -> None:
    # The heartbeat backfill is deliberately not reversed. It only ever filled
    # NULLs on `running` rows, and restoring those NULLs would put back the
    # defect this migration exists to fix -- runs the reconciler cannot see.
    # Nothing reads `lease_token` at 0027, so dropping it is complete.
    op.drop_column("rule_applications", "lease_token")
