"""Platform settings and announcements (Admin Control Center phase 10,
D-019).

* ``platform_settings``: one row per platform-wide setting (maintenance
  mode first), a small JSON value each.
* ``platform_announcements``: banners shown to signed-in merchants while
  active.

Both sit above every tenant, like the operators who write them.
``downgrade()`` drops both tables.

Revision ID: 0057
Revises: 0056
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        *_base(),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column(
            "value", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("updated_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["updated_by_admin_id"], ["platform_admins.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("key", name="uq_platform_settings_key"),
    )
    op.create_table(
        "platform_announcements",
        *_base(),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False, server_default=""),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"], ["platform_admins.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_platform_announcements_window", "platform_announcements", ["starts_at", "ends_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_platform_announcements_window", table_name="platform_announcements")
    op.drop_table("platform_announcements")
    op.drop_table("platform_settings")
