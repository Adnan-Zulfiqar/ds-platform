"""Extend the supplier-snapshot pattern to title and brand.

Revision ID: 0014
Revises: 0013
Created: 2026-08-03 12:32:00+00:00

Product Editor stage 2. `title` and `brand` become merchant-editable via the
new `PATCH /products/{id}`, which means they need the same protection
`description`/`supplier_description` (migration 0013) already has: a sync
must not silently revert a merchant's edit back to whatever the supplier
currently says.

Adds `supplier_title`/`supplier_brand`, and backfills them from the current
`title`/`brand` for every existing row. This backfill matters in a way
`supplier_description`'s did not: `title` is `NOT NULL` and every existing
product already has a real value in it (unlike `description`, which was
always `NULL` before stage 1). Leaving `supplier_title` `NULL` for those rows
would make `title == supplier_title` false immediately after this migration
runs — read by `ProductImportService._upsert` as "already diverged," which
would silently freeze every existing product's title against future syncs
even though no merchant has actually edited anything yet.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("supplier_title", sa.String(length=512), nullable=True))
    op.add_column("products", sa.Column("supplier_brand", sa.String(length=255), nullable=True))

    op.execute(sa.text("UPDATE products SET supplier_title = title, supplier_brand = brand"))


def downgrade() -> None:
    op.drop_column("products", "supplier_brand")
    op.drop_column("products", "supplier_title")
