"""Index store_listings for Drafts vs Products publication queries.

Revision ID: 0015
Revises: 0014
Created: 2026-08-06 18:45:00+00:00

Product Workspace V2 Stage 0. Drafts and Products are projections over the same
``products`` aggregate: a product is "published" when at least one
``store_listings`` row is ``synced``. List endpoints use EXISTS / NOT EXISTS
against ``(tenant_id, product_id, status)``; this index makes those predicates
index-friendly without changing row data.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_store_listings_tenant_product_status",
        "store_listings",
        ["tenant_id", "product_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_store_listings_tenant_product_status", table_name="store_listings")
