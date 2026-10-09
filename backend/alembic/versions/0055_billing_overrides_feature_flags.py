"""Plan overrides and feature flags (Admin Control Center phase 8, D-019).

* ``tenant_subscriptions``: four nullable or defaulted columns for an
  operator's time-limited plan override. Existing rows have none.
* ``feature_flags``: platform-wide switches, seeded **enabled**, so nothing
  changes until an operator turns one off.
* ``tenant_feature_flags``: one workspace's override of a switch.

``downgrade()`` drops all of it.

Revision ID: 0055
Revises: 0054
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

#: The switches the application enforces (see ``app.services.feature_flags``).
SEED = (
    ("ai_bulk_pipeline", "Bulk AI optimisation runs (AI Studio)."),
    ("supplier_auto_ordering", "Placing paid orders on AliExpress automatically."),
    ("channel_publishing", "Publishing new listings to Shopify, eBay and WooCommerce."),
)


def _timestamps() -> list[sa.Column[object]]:
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
    op.add_column("tenant_subscriptions", sa.Column("plan_override", sa.String(16), nullable=True))
    op.add_column(
        "tenant_subscriptions",
        sa.Column("plan_override_ai", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "tenant_subscriptions",
        sa.Column("plan_override_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_subscriptions", sa.Column("plan_override_reason", sa.String(500), nullable=True)
    )

    flags = op.create_table(
        "feature_flags",
        *_timestamps(),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("key", name="uq_feature_flags_key"),
    )
    op.bulk_insert(
        flags,
        [
            {"id": uuid.uuid4(), "key": key, "description": text, "enabled": True}
            for key, text in SEED
        ],
    )

    op.create_table(
        "tenant_feature_flags",
        *_timestamps(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_feature_flags_key"),
    )
    for column in ("tenant_id", "created_at", "deleted_at"):
        op.create_index(f"ix_tenant_feature_flags_{column}", "tenant_feature_flags", [column])


def downgrade() -> None:
    for column in ("deleted_at", "created_at", "tenant_id"):
        op.drop_index(f"ix_tenant_feature_flags_{column}", table_name="tenant_feature_flags")
    op.drop_table("tenant_feature_flags")
    op.drop_table("feature_flags")
    for column in (
        "plan_override_reason",
        "plan_override_until",
        "plan_override_ai",
        "plan_override",
    ):
        op.drop_column("tenant_subscriptions", column)
