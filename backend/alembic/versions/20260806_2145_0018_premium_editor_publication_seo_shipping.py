"""Premium editor — publication URLs, SEO planning, shipping customs.

Revision ID: 0018
Revises: 0017
Created: 2026-08-06 21:45:00+00:00

Additive only. Does not change OAuth scopes in the database.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "store_listings",
        sa.Column("external_handle", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("external_graphql_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("shop_domain", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("storefront_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("admin_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("online_store_published", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_listings",
        sa.Column("last_failed_sync_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "products",
        sa.Column(
            "requires_shipping",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column("products", sa.Column("hs_code", sa.String(length=32), nullable=True))
    op.add_column(
        "products",
        sa.Column("country_of_origin", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("customs_description", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("handling_time_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("weight_unit", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("dimension_unit", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column(
            "redirect_old_handle",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column("products", sa.Column("og_title", sa.String(length=512), nullable=True))
    op.add_column(
        "products",
        sa.Column("og_description", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column(
            "search_topics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "seo_planning",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "seo_planning")
    op.drop_column("products", "search_topics")
    op.drop_column("products", "og_description")
    op.drop_column("products", "og_title")
    op.drop_column("products", "redirect_old_handle")
    op.drop_column("products", "dimension_unit")
    op.drop_column("products", "weight_unit")
    op.drop_column("products", "handling_time_days")
    op.drop_column("products", "customs_description")
    op.drop_column("products", "country_of_origin")
    op.drop_column("products", "hs_code")
    op.drop_column("products", "requires_shipping")
    op.drop_column("store_listings", "last_failed_sync_at")
    op.drop_column("store_listings", "published_at")
    op.drop_column("store_listings", "online_store_published")
    op.drop_column("store_listings", "admin_url")
    op.drop_column("store_listings", "storefront_url")
    op.drop_column("store_listings", "shop_domain")
    op.drop_column("store_listings", "external_graphql_id")
    op.drop_column("store_listings", "external_handle")
