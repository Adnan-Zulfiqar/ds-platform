"""Outbound email for notifications (Track E3).

* ``notifications.email_status`` / ``emailed_at`` — the outbox columns,
  nullable; existing rows stay null and are never emailed retroactively.
* Partial index on pending rows per tenant, for the sweep.
* ``notification_email_preferences`` — per-user opted-in kinds.

Additive only; ``downgrade()`` removes exactly these objects.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_PREFS = "notification_email_preferences"


def upgrade() -> None:
    op.add_column("notifications", sa.Column("email_status", sa.String(16), nullable=True))
    op.add_column(
        "notifications", sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_notifications_tenant_email_pending",
        "notifications",
        ["tenant_id", "created_at"],
        postgresql_where=sa.text("email_status = 'pending'"),
    )
    op.create_table(
        _PREFS,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kinds", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tenant_id", "user_id", name="uq_notification_email_preferences_tenant_user"
        ),
    )
    for column in ("tenant_id", "created_at", "deleted_at", "user_id"):
        op.create_index(f"ix_{_PREFS}_{column}", _PREFS, [column])


def downgrade() -> None:
    for column in ("user_id", "deleted_at", "created_at", "tenant_id"):
        op.drop_index(f"ix_{_PREFS}_{column}", table_name=_PREFS)
    op.drop_table(_PREFS)
    op.drop_index("ix_notifications_tenant_email_pending", table_name="notifications")
    op.drop_column("notifications", "emailed_at")
    op.drop_column("notifications", "email_status")
