"""Authentication schema: roles, role assignments, refresh tokens, user changes.

Migration ``0001`` is left untouched. This migration carries the Phase 1 changes
forward from it, including the three destructive column changes to ``users``
described in ``app/models/user.py``.

Those drops are safe here specifically because ``0001`` had never been applied
to any live database when Phase 1 began — there is no data to lose. Were that
not the case, ``full_name`` would need a backfill into ``first_name``/
``last_name`` and the ``role`` column a backfill into ``user_roles`` before
either could be dropped.

Revision ID: 0002
Revises: 0001
Created: 2026-07-30 22:00:00+00:00

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Role identifiers are derived deterministically from their names rather than
# generated randomly, so that a given role has the same id in every environment.
# That makes fixtures portable and lets a support query compare ids across
# staging and production without a lookup.
_ROLE_NAMESPACE = uuid.UUID("6f1d6a2e-9c5b-4f38-9a4a-4a6f2b1c8d70")

_ROLES: list[dict[str, str]] = [
    {"name": "owner", "description": "Full access including billing. Cannot be removed."},
    {"name": "admin", "description": "Full operational access."},
    {"name": "member", "description": "Day-to-day operations."},
    {"name": "viewer", "description": "Read-only access."},
]


def upgrade() -> None:
    # ---------------------------------------------------------------- roles
    roles_table = op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_index("ix_roles_created_at", "roles", ["created_at"])
    op.create_index("ix_roles_name", "roles", ["name"])

    # Seeded in the migration rather than by application startup code: the
    # application cannot function without these rows, so they are part of the
    # schema contract, and a startup seed would race between replicas.
    op.bulk_insert(
        roles_table,
        [
            {
                "id": uuid.uuid5(_ROLE_NAMESPACE, role["name"]),
                "name": role["name"],
                "description": role["description"],
            }
            for role in _ROLES
        ],
    )

    # ----------------------------------------------------------- user_roles
    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_roles_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_user_roles_role_id_roles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )
    op.create_index("ix_user_roles_role_id_user_id", "user_roles", ["role_id", "user_id"])

    # ------------------------------------------------------- refresh_tokens
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_refresh_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])
    op.create_index("ix_refresh_tokens_revoked_at", "refresh_tokens", ["revoked_at"])
    op.create_index("ix_refresh_tokens_user_active", "refresh_tokens", ["user_id", "revoked_at"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])

    # ---------------------------------------------------------- users table
    op.add_column("users", sa.Column("first_name", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=128), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_verified", sa.Boolean(), server_default="false", nullable=False),
    )

    op.drop_column("users", "full_name")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "role")

    # The enum type survives dropping the column that used it and would block a
    # later type of the same name.
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # Restore the Phase 0 shape. The role enum type must exist again before the
    # column that references it can be added.
    user_role_enum = sa.Enum("owner", "admin", "member", "viewer", name="user_role")
    user_role_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column("role", user_role_enum, nullable=False, server_default="member"),
    )
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("full_name", sa.String(length=255), nullable=True))

    # Reassemble full_name from the parts before dropping them, so a downgrade
    # does not silently discard names. NULLIF collapses the empty string that
    # concatenating two NULLs produces back to NULL.
    op.execute(
        "UPDATE users SET full_name = NULLIF(TRIM(BOTH ' ' FROM "
        "COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')), '')"
    )

    op.drop_column("users", "is_verified")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")

    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_active", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_revoked_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_user_roles_role_id_user_id", table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_index("ix_roles_name", table_name="roles")
    op.drop_index("ix_roles_created_at", table_name="roles")
    op.drop_table("roles")
