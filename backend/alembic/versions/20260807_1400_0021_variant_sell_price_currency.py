"""Stamp the currency a variant's sell_price was actually computed in.

Revision ID: 0021
Revises: 0020
Created: 2026-08-07 14:00:00+00:00

Closes the last mislabeling gap the M24A pricing-currency work left open:
`ProductVariant.currency` is the *supplier's* currency (from AliExpress), but
nothing recorded what currency `sell_price`/`compare_at_price` were actually
written in once `PricingEngine.apply_draft_variant_pricing` converted and
marked them up into the selling currency. A variant priced for a GBP store,
then published after the merchant switches to a USD-currency store, would
silently send a GBP number labelled USD to Shopify — the same defect class
this feature exists to prevent, just moved into persisted data instead of a
preview response.

Existing rows keep `sell_price_currency = NULL`. A row with a non-null
`sell_price` but a null `sell_price_currency` means "priced before this
column existed" — read as unverified/needs-recalculation, never assumed to
match today's resolved selling currency. Never backfilled from `currency`
(the supplier currency): that would assert something not actually known —
whether a given historical `sell_price` was ever actually converted, or was
copied from the supplier cost, is exactly what is unknown here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_variants",
        sa.Column("sell_price_currency", sa.String(length=3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_variants", "sell_price_currency")
