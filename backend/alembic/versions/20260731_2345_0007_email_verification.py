"""Phase 7 — email verification token table.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_verification_tokens",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_email_verification_tokens_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_verification_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_verification_tokens")),
    )
    op.create_index(
        op.f("ix_email_verification_tokens_created_at"),
        "email_verification_tokens",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_verification_tokens_deleted_at"),
        "email_verification_tokens",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_verification_tokens_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_email_verification_tokens_tenant_id"),
        "email_verification_tokens",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_verification_tokens_tenant_user",
        "email_verification_tokens",
        ["tenant_id", "user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_email_verification_tokens_user_id"),
        "email_verification_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_email_verification_tokens_user_id"), table_name="email_verification_tokens"
    )
    op.drop_index(
        "ix_email_verification_tokens_tenant_user", table_name="email_verification_tokens"
    )
    op.drop_index(
        op.f("ix_email_verification_tokens_tenant_id"), table_name="email_verification_tokens"
    )
    op.drop_index("ix_email_verification_tokens_hash", table_name="email_verification_tokens")
    op.drop_index(
        op.f("ix_email_verification_tokens_deleted_at"), table_name="email_verification_tokens"
    )
    op.drop_index(
        op.f("ix_email_verification_tokens_created_at"), table_name="email_verification_tokens"
    )
    op.drop_table("email_verification_tokens")
