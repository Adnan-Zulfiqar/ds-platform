"""Platform operator roles, sessions and richer audit (Admin Control Center
phase 1, owner decision D-018).

* ``platform_admins.role``: existing operators become ``super_admin``, which
  is exactly what they could do before roles existed.
* ``platform_admin_sessions``: one row per sign-in, so a session can be
  listed and revoked at once.
* ``platform_admin_audit``: user agent, request id, the actor's role, the
  outcome and a generic target. Nullable (or defaulted), so existing rows are
  valid as they are.

``downgrade()`` drops all of it.

Revision ID: 0052
Revises: 0051
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_admins",
        sa.Column("role", sa.String(32), nullable=False, server_default="super_admin"),
    )

    op.create_table(
        "platform_admin_sessions",
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(64), nullable=True),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["platform_admins.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_platform_admin_sessions_admin_id", "platform_admin_sessions", ["admin_id"])

    for column in (
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("actor_role", sa.String(32), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="success"),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
    ):
        op.add_column("platform_admin_audit", column)
    op.create_index("ix_platform_admin_audit_outcome", "platform_admin_audit", ["outcome"])


def downgrade() -> None:
    op.drop_index("ix_platform_admin_audit_outcome", table_name="platform_admin_audit")
    for name in ("target_id", "target_type", "outcome", "actor_role", "request_id", "user_agent"):
        op.drop_column("platform_admin_audit", name)
    op.drop_index("ix_platform_admin_sessions_admin_id", table_name="platform_admin_sessions")
    op.drop_table("platform_admin_sessions")
    op.drop_column("platform_admins", "role")
