"""Subscription state per workspace (Track E6).

New table ``tenant_subscriptions`` only; nothing existing changes.
``downgrade()`` drops exactly this table.

Revision ID: 0047
Revises: 0046
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_T = "tenant_subscriptions"


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
        sa.Column("stripe_customer_id", sa.String(64), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(64), nullable=True),
        sa.Column("plan", sa.String(16), nullable=True),
        sa.Column("ai_addon", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.String(32), server_default="none", nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("stripe_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_subscriptions_tenant"),
        sa.UniqueConstraint("stripe_customer_id", name="uq_tenant_subscriptions_customer"),
        sa.UniqueConstraint("stripe_subscription_id", name="uq_tenant_subscriptions_subscription"),
    )
    for column in ("tenant_id", "created_at", "deleted_at"):
        op.create_index(f"ix_{_T}_{column}", _T, [column])


def downgrade() -> None:
    for column in ("deleted_at", "created_at", "tenant_id"):
        op.drop_index(f"ix_{_T}_{column}", table_name=_T)
    op.drop_table(_T)
