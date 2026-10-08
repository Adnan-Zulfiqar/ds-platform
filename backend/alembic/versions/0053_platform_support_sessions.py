"""Platform support sessions (Admin Control Center phase 4, owner decision
D-019).

One row per time-limited window in which an operator may change one
workspace. ``downgrade()`` drops the table.

Revision ID: 0053
Revises: 0052
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_support_sessions",
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
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_reason", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["platform_admins.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    # The question every workspace change asks: does this operator have an
    # open session for this workspace?
    op.create_index(
        "ix_platform_support_sessions_admin_tenant",
        "platform_support_sessions",
        ["admin_id", "tenant_id", "expires_at"],
    )
    op.create_index(
        "ix_platform_support_sessions_tenant", "platform_support_sessions", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_platform_support_sessions_tenant", table_name="platform_support_sessions")
    op.drop_index(
        "ix_platform_support_sessions_admin_tenant", table_name="platform_support_sessions"
    )
    op.drop_table("platform_support_sessions")
