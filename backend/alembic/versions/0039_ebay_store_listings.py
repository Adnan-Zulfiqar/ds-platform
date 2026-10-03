"""eBay stores and listings (EBAY-C3, D-C3-2).

Schema changes to existing tables, announced in
``docs/ebay/EBAY_C3_PROPOSAL.md`` and approved by the owner:

* ``ebay_listing_defaults.store_id`` — nullable FK to ``stores`` (SET NULL):
  the ``Store`` row that represents one eBay marketplace for a workspace.
* ``store_listings.external_offer_id`` and ``external_sku`` — nullable; eBay
  publishes an inventory item (by SKU) through an offer, separately from the
  listing id kept in ``external_product_id``. Shopify rows leave them null.

Additive and nullable only, so no existing row changes. ``downgrade()``
removes exactly these columns.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ebay_listing_defaults",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ebay_listing_defaults_store_id_stores",
        "ebay_listing_defaults",
        "stores",
        ["store_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ebay_listing_defaults_store_id", "ebay_listing_defaults", ["store_id"])
    op.add_column("store_listings", sa.Column("external_offer_id", sa.String(32), nullable=True))
    op.add_column("store_listings", sa.Column("external_sku", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("store_listings", "external_sku")
    op.drop_column("store_listings", "external_offer_id")
    op.drop_index("ix_ebay_listing_defaults_store_id", table_name="ebay_listing_defaults")
    op.drop_constraint(
        "fk_ebay_listing_defaults_store_id_stores", "ebay_listing_defaults", type_="foreignkey"
    )
    op.drop_column("ebay_listing_defaults", "store_id")
