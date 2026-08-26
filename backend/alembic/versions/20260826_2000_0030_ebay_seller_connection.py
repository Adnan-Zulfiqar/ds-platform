"""eBay seller OAuth connection (EBAY-C1).

Additive. 0001-0029 are not modified: this adds one table and one enum type and
alters nothing that exists, so a rollback is a clean drop with no data migration
to reverse.

**This is the migration that makes eBay personal data real in this platform.**
Until now the only eBay table was the compliance ledger, which deliberately
holds none. ``ebay_connections.ebay_user_id`` is eBay's immutable account
identifier, and storing it is what obliges EBAY-C1 to register a deletion owner
under the EBAY-C0 storage contract. The release guard in
``tests/unit/test_ebay_c0_deletion_governance.py`` fails if the declaration and
the eraser disagree, so the table cannot ship without one.

Two unique constraints, and they answer different questions:

* ``uq_ebay_connections_tenant_id`` — one eBay account per workspace.
* ``uq_ebay_connections_ebay_user_id`` — **global**. One eBay seller cannot be
  attached to two workspaces. Enforced here rather than by a lookup because a
  check-then-insert races, and a cross-tenant read would be an existence oracle.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_STATUS = postgresql.ENUM(
    "pending",
    "connected",
    "reconnect_required",
    "error",
    name="ebay_connection_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _STATUS.create(bind, checkfirst=True)

    op.create_table(
        "ebay_connections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
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
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("ebay_user_id", sa.String(length=128), nullable=False),
        sa.Column("ebay_username", sa.String(length=255), nullable=True),
        sa.Column("marketplace_id", sa.String(length=32), nullable=True),
        sa.Column("account_type", sa.String(length=32), nullable=True),
        sa.Column("encrypted_access_token", sa.String(length=4096), nullable=True),
        sa.Column("encrypted_refresh_token", sa.String(length=4096), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", _STATUS, nullable=False, server_default="pending"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconnect_reason", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_ebay_connections_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        # SET NULL, not CASCADE: removing the person who clicked Connect must
        # not delete the workspace's working integration.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ebay_connections_user_id_users",
            ondelete="SET NULL",
        ),
    )

    op.create_unique_constraint("uq_ebay_connections_tenant_id", "ebay_connections", ["tenant_id"])
    op.create_unique_constraint(
        "uq_ebay_connections_ebay_user_id", "ebay_connections", ["ebay_user_id"]
    )
    op.create_index("ix_ebay_connections_tenant_id", "ebay_connections", ["tenant_id"])
    op.create_index("ix_ebay_connections_status", "ebay_connections", ["status"])
    op.create_index(
        "ix_ebay_connections_status_expiry",
        "ebay_connections",
        ["status", "access_token_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ebay_connections_status_expiry", table_name="ebay_connections")
    op.drop_index("ix_ebay_connections_status", table_name="ebay_connections")
    op.drop_index("ix_ebay_connections_tenant_id", table_name="ebay_connections")
    op.drop_constraint("uq_ebay_connections_ebay_user_id", "ebay_connections", type_="unique")
    op.drop_constraint("uq_ebay_connections_tenant_id", "ebay_connections", type_="unique")
    op.drop_table("ebay_connections")
    # After the table, and only then: an enum still referenced by a column
    # cannot be dropped, and an orphan type makes the next upgrade fail on
    # CREATE TYPE.
    _STATUS.drop(op.get_bind(), checkfirst=True)
