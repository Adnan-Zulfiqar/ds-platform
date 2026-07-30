"""Initial multi-tenancy schema: tenants and users.

Establishes the two tables every later phase depends on. Business tables added
in subsequent phases carry a ``tenant_id`` foreign key into ``tenants``.

Revision ID: 0001
Revises:
Created: 2026-07-30 12:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "trial",
                "active",
                "suspended",
                "cancelled",
                name="tenant_status",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("default_currency", sa.String(length=3), server_default="USD", nullable=False),
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
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'",
            name="ck_tenants_slug_is_valid_dns_label",
        ),
        sa.CheckConstraint(
            "char_length(default_currency) = 3",
            name="ck_tenants_currency_is_iso4217",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_index("ix_tenants_created_at", "tenants", ["created_at"])
    op.create_index("ix_tenants_deleted_at", "tenants", ["deleted_at"])
    op.create_index("ix_tenants_is_active", "tenants", ["is_active"])
    op.create_index("ix_tenants_slug", "tenants", ["slug"])
    op.create_index("ix_tenants_status", "tenants", ["status"])

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "role",
            sa.Enum("owner", "admin", "member", "viewer", name="user_role"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_users_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
    )
    op.create_index("ix_users_created_at", "users", ["created_at"])
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_tenant_active", "users", ["tenant_id", "deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_users_tenant_active", table_name="users")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_tenants_status", table_name="tenants")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_index("ix_tenants_is_active", table_name="tenants")
    op.drop_index("ix_tenants_deleted_at", table_name="tenants")
    op.drop_index("ix_tenants_created_at", table_name="tenants")
    op.drop_table("tenants")

    # Enum types are not dropped by drop_table and would collide with a
    # subsequent upgrade.
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="tenant_status").drop(op.get_bind(), checkfirst=True)
