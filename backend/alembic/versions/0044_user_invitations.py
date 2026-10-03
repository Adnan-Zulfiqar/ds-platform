"""Team invitations (Track E4).

New table ``user_invitations`` only; nothing existing changes.
``downgrade()`` drops exactly this table and its indexes.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

_T = "user_invitations"


def upgrade() -> None:
    op.create_table(
        _T,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["accepted_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("token_hash", name="uq_user_invitations_token_hash"),
    )
    for column in ("tenant_id", "created_at", "deleted_at"):
        op.create_index(f"ix_{_T}_{column}", _T, [column])
    op.create_index(
        "uq_user_invitations_tenant_email_open",
        _T,
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text(
            "accepted_at IS NULL AND revoked_at IS NULL AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_user_invitations_tenant_email_open", table_name=_T)
    for column in ("deleted_at", "created_at", "tenant_id"):
        op.drop_index(f"ix_{_T}_{column}", table_name=_T)
    op.drop_table(_T)
