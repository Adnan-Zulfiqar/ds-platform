"""Record which text is live on each channel listing (review finding E-1).

Adds ``store_listings.content_source`` (``product`` | ``ai_version``) and
``content_version_id``. Every existing listing was published from the draft
text, so the server default ``product`` is the truthful back-fill; no row is
guessed into ``ai_version``.

``content_version_id`` references ``product_versions`` through a composite
``(tenant_id, product_id, id)`` key, so the database itself refuses a listing
that claims another product's (or another tenant's) version is live. That key
needs a matching unique constraint on ``product_versions``, added here.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_SOURCE_VALUES = ("product", "ai_version")


def upgrade() -> None:
    bind = op.get_bind()
    sa.Enum(*_SOURCE_VALUES, name="listing_content_source").create(bind, checkfirst=True)
    source_enum = postgresql.ENUM(name="listing_content_source", create_type=False)

    op.create_unique_constraint(
        "uq_product_versions_tenant_product_id",
        "product_versions",
        ["tenant_id", "product_id", "id"],
    )
    op.add_column(
        "store_listings",
        sa.Column("content_source", source_enum, nullable=False, server_default="product"),
    )
    op.add_column(
        "store_listings",
        sa.Column("content_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_store_listings_content_version_product_tenant",
        "store_listings",
        "product_versions",
        ["tenant_id", "product_id", "content_version_id"],
        ["tenant_id", "product_id", "id"],
    )
    op.create_check_constraint(
        op.f("ck_store_listings_content_source_version"),
        "store_listings",
        "(content_source = 'ai_version' AND content_version_id IS NOT NULL) "
        "OR (content_source = 'product' AND content_version_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_store_listings_content_source_version"), "store_listings", type_="check"
    )
    op.drop_constraint(
        "fk_store_listings_content_version_product_tenant",
        "store_listings",
        type_="foreignkey",
    )
    op.drop_column("store_listings", "content_version_id")
    op.drop_column("store_listings", "content_source")
    op.drop_constraint("uq_product_versions_tenant_product_id", "product_versions", type_="unique")
    postgresql.ENUM(name="listing_content_source").drop(op.get_bind(), checkfirst=True)
