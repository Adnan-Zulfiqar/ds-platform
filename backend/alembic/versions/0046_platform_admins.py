"""Platform operators and their audit trail (Track E5, decision D-015).

New tables only: ``platform_admins`` and ``platform_admin_audit``. Nothing
existing changes. ``downgrade()`` drops exactly these.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "platform_admins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("encrypted_totp_secret", sa.Text(), nullable=False),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_platform_admins_email"),
    )
    op.create_index("ix_platform_admins_deleted_at", "platform_admins", ["deleted_at"])
    op.create_table(
        "platform_admin_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        *_timestamps(),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "detail", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["platform_admins.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_tenant_id"], ["tenants.id"], ondelete="SET NULL"),
    )
    for column in ("admin_id", "action", "target_tenant_id", "created_at"):
        op.create_index(f"ix_platform_admin_audit_{column}", "platform_admin_audit", [column])


def downgrade() -> None:
    for column in ("created_at", "target_tenant_id", "action", "admin_id"):
        op.drop_index(f"ix_platform_admin_audit_{column}", table_name="platform_admin_audit")
    op.drop_table("platform_admin_audit")
    op.drop_index("ix_platform_admins_deleted_at", table_name="platform_admins")
    op.drop_table("platform_admins")
