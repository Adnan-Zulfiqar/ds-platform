"""Global uniqueness for Shopify shop domains (audit A-01).

Revision ID: 0012
Revises: 0011
Created: 2026-08-03 08:00:00+00:00

Webhook routing previously scanned up to 1000 connected rows and took the
first ``shop_domain`` match. Combined with a uniqueness constraint that was
only ``(tenant_id, shop_domain)``, two tenants could connect the same shop and
inbound HMAC-valid webhooks could write orders into the wrong workspace.

This migration:

1. Removes duplicate ``shop_domain`` rows (keeps the earliest ``created_at``,
   then lowest ``id``), so the unique index can apply on a dirty database.
2. Adds a **global** unique constraint on ``shop_domain``.

The per-tenant unique ``(tenant_id, shop_domain)`` remains — it is redundant
after this change but harmless, and dropping it is unrelated noise.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep the oldest row per shop_domain; delete the rest. Connections
    # hard-delete; orphaned ``stores`` rows (if any) are left for a later
    # housekeeping pass rather than cascading into catalogue side-effects.
    op.execute(
        sa.text(
            """
            DELETE FROM shopify_connections AS duplicate
            USING shopify_connections AS keeper
            WHERE duplicate.shop_domain = keeper.shop_domain
              AND (
                    duplicate.created_at > keeper.created_at
                 OR (
                        duplicate.created_at = keeper.created_at
                    AND duplicate.id > keeper.id
                    )
              )
            """
        )
    )
    op.create_unique_constraint(
        "uq_shopify_connections_shop_domain",
        "shopify_connections",
        ["shop_domain"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_shopify_connections_shop_domain",
        "shopify_connections",
        type_="unique",
    )
