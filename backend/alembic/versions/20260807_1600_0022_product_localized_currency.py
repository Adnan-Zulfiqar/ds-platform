"""Distinguish the supplier's native currency from the currency actually requested.

Revision ID: 0022
Revises: 0021
Created: 2026-08-07 16:00:00+00:00

M24B live-traced two real gaps in `Product.currency`:

1. It was set straight from `ae_item_base_info_dto.currency_code`, which
   AliExpress never localizes regardless of `target_currency` — a live GB/GBP
   request for a real product returned `currency_code: "CNY"` at that level
   while every SKU (which `cost_price_min`/`cost_price_max` are actually
   computed from) genuinely reported `"GBP"`. Pairing the min/max numbers
   with the wrong currency was a real, reproduced mislabeling bug, not a
   hypothetical one. `map_product` now derives `currency` from the SKUs
   themselves; `supplier_native_currency` (new) keeps the seller's own
   listing currency as audit-only information, never used for pricing math.

2. Nothing recorded which `target_currency` was actually requested from
   AliExpress on the last import/refresh. Every refresh/sync call path
   previously omitted the field entirely and silently defaulted to `"USD"`
   regardless of the product's actual destination — `import_currency` (new)
   pairs with the existing `import_ship_to_country` to close that gap.

Existing rows: both columns start `NULL`. Never backfilled from `currency` —
whether a historical row's `currency` came from a localized SKU price, the
unlocalized base currency, or a manual edit is exactly what is not known
after the fact. A `NULL` here means "imported before this distinction
existed," not "confirmed CNY" or "confirmed native."
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("supplier_native_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("import_currency", sa.String(length=3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "import_currency")
    op.drop_column("products", "supplier_native_currency")
