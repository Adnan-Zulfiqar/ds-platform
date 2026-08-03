"""Separate supplier description snapshot from merchant-editable description.

Revision ID: 0013
Revises: 0012
Created: 2026-08-03 11:50:00+00:00

Product Editor Stage 1. AliExpress's product-detail response carries a real
HTML description (``detail``) that was previously parsed by nothing, mapped
by nothing, and left permanently ``NULL`` in ``products.description`` —
confirmed against the committed live fixture during the gap audit
(``docs/PRODUCT_EDITOR_GAP_AUDIT.md``).

``products.description`` already existed and becomes the merchant-editable
field, seeded from the supplier on first import. This migration adds
``supplier_description``: the always-current supplier snapshot, refreshed on
every sync regardless of what the merchant has done to ``description`` — the
mechanism that lets a future write API edit ``description`` without a
subsequent sync silently reverting that edit (see
``ProductImportService._upsert``).

Nullable, no backfill: every existing row's ``description`` is already
``NULL`` (nothing ever populated it), so there is nothing to migrate forward.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("supplier_description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "supplier_description")
