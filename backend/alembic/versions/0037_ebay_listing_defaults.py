"""eBay listing defaults (EBAY-C2).

One row per workspace and marketplace: the seller's chosen fulfillment,
payment and return policy ids and inventory location key. Physical rows
bound to ``ebay_connections`` with ``ON DELETE CASCADE`` — the ids belong to
that seller and are erased with the connection (see
``docs/ebay/EBAY_C2_LISTING_SETUP.md``). A new table only; nothing existing
changes, and ``downgrade()`` drops it.

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_TABLE = "ebay_listing_defaults"


def upgrade() -> None:
    op.create_table(
        _TABLE,
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
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("fulfillment_policy_id", sa.String(64), nullable=False),
        sa.Column("payment_policy_id", sa.String(64), nullable=False),
        sa.Column("return_policy_id", sa.String(64), nullable=False),
        sa.Column("merchant_location_key", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["ebay_connections.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "tenant_id", "marketplace_id", name="uq_ebay_listing_defaults_tenant_marketplace"
        ),
    )
    op.create_index(f"ix_{_TABLE}_created_at", _TABLE, ["created_at"])
    op.create_index(f"ix_{_TABLE}_tenant_id", _TABLE, ["tenant_id"])
    op.create_index(f"ix_{_TABLE}_connection_id", _TABLE, ["connection_id"])


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_connection_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_tenant_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_created_at", table_name=_TABLE)
    op.drop_table(_TABLE)
