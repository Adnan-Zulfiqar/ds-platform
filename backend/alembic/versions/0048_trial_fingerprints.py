"""Stores that have used a free trial (Track E6b).

New table ``trial_fingerprints`` only. ``downgrade()`` drops it.

Revision ID: 0048
Revises: 0047
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trial_fingerprints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("first_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["first_tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("fingerprint", name="uq_trial_fingerprints_fingerprint"),
    )


def downgrade() -> None:
    op.drop_table("trial_fingerprints")
