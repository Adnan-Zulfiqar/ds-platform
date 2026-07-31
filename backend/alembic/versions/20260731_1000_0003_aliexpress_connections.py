"""AliExpress integration connections.

Adds the table holding a tenant's connection to the AliExpress Open Platform.
Migrations ``0001`` and ``0002`` are untouched.

Every credential column stores Fernet ciphertext, never plaintext. The column
names say so, and ``app.core.encryption`` is the only thing that reads them.

Revision ID: 0003
Revises: 0002
Created: 2026-07-31 10:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aliexpress_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("app_key", sa.String(length=64), nullable=False),
        # Ciphertext. Sized for base64 Fernet output, which is roughly 1.4x the
        # plaintext plus fixed overhead.
        sa.Column("encrypted_app_secret", sa.String(length=1024), nullable=False),
        sa.Column("encrypted_access_token", sa.String(length=2048), nullable=True),
        sa.Column("encrypted_refresh_token", sa.String(length=2048), nullable=True),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "connected",
                "expired",
                "error",
                name="integration_status",
            ),
            nullable=False,
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_aliexpress_connections_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        # SET NULL, not CASCADE: removing the user who authorised a connection
        # must not destroy a working tenant-wide integration.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_aliexpress_connections_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_aliexpress_connections"),
        sa.UniqueConstraint("tenant_id", name="uq_aliexpress_connections_tenant_id"),
    )

    op.create_index(
        "ix_aliexpress_connections_created_at", "aliexpress_connections", ["created_at"]
    )
    op.create_index("ix_aliexpress_connections_tenant_id", "aliexpress_connections", ["tenant_id"])
    op.create_index("ix_aliexpress_connections_status", "aliexpress_connections", ["status"])
    # Supports the health-check job scanning for tokens near expiry.
    op.create_index(
        "ix_aliexpress_connections_status_expiry",
        "aliexpress_connections",
        ["status", "token_expiry"],
    )


def downgrade() -> None:
    op.drop_index("ix_aliexpress_connections_status_expiry", table_name="aliexpress_connections")
    op.drop_index("ix_aliexpress_connections_status", table_name="aliexpress_connections")
    op.drop_index("ix_aliexpress_connections_tenant_id", table_name="aliexpress_connections")
    op.drop_index("ix_aliexpress_connections_created_at", table_name="aliexpress_connections")
    op.drop_table("aliexpress_connections")

    # The enum type survives drop_table and would collide with a later upgrade.
    sa.Enum(name="integration_status").drop(op.get_bind(), checkfirst=True)
