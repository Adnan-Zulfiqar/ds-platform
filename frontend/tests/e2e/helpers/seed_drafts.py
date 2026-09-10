"""Seed draft rows from stdin JSON — credentials never touch argv."""

from __future__ import annotations

import json
import sys
import uuid

import sqlalchemy as sa


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"invalid stdin json: {exc}", file=sys.stderr)
        sys.exit(2)

    url = payload.get("databaseUrl")
    tenant_id = payload.get("tenantId")
    count = payload.get("count")
    published = payload.get("published", 0)
    flagged = payload.get("flagged", 0)
    prefix = payload.get("prefix", "Impact draft")

    if not isinstance(url, str) or not url.strip():
        print("missing databaseUrl", file=sys.stderr)
        sys.exit(2)
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        print("missing tenantId", file=sys.stderr)
        sys.exit(2)
    if not isinstance(count, int) or count < 0:
        print("invalid count", file=sys.stderr)
        sys.exit(2)
    if not isinstance(published, int) or published < 0:
        print("invalid published", file=sys.stderr)
        sys.exit(2)
    if not isinstance(flagged, int) or flagged < 0:
        print("invalid flagged", file=sys.stderr)
        sys.exit(2)
    if not isinstance(prefix, str):
        print("invalid prefix", file=sys.stderr)
        sys.exit(2)

    engine = sa.create_engine(url)
    with engine.begin() as connection:
        for index in range(count):
            no_freight = index < flagged
            product_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    """
                    INSERT INTO products
                        (id, tenant_id, source, external_id, title, status,
                         cost_price_min, shipping_cost, currency, sell_price,
                         stock_quantity, needs_review, pricing_review_reasons,
                         created_at, updated_at)
                    VALUES
                        (:id, :tenant, 'manual', :external, :title, 'draft',
                         10.00, :shipping, 'GBP', 30.00, 25, false, '[]'::jsonb,
                         now(), now())
                    """
                ),
                {
                    "id": product_id,
                    "tenant": tenant_id,
                    "external": f"seed-{product_id.hex[:12]}",
                    "title": f"{prefix} {index}",
                    "shipping": None if no_freight else 4.00,
                },
            )
            if index < published:
                store_id = uuid.uuid4()
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO stores
                            (id, tenant_id, name, slug, platform, status, currency,
                             timezone, settings, inventory_sync_enabled,
                             pricing_sync_enabled, order_sync_enabled, health_score,
                             created_at, updated_at)
                        VALUES
                            (:id, :tenant, :name, :slug, 'shopify', 'connected', 'GBP',
                             'UTC', '{}'::jsonb, true, true, true, 100, now(), now())
                        """
                    ),
                    {
                        "id": store_id,
                        "tenant": tenant_id,
                        "name": f"Seed store {store_id.hex[:6]}",
                        "slug": f"seed-{store_id.hex[:10]}",
                    },
                )
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO store_listings
                            (id, tenant_id, store_id, product_id, external_product_id,
                             external_variant_map, inventory_item_map, status,
                             created_at, updated_at)
                        VALUES
                            (gen_random_uuid(), :tenant, :store, :product, :external,
                             '{}'::jsonb, '{}'::jsonb, 'synced', now(), now())
                        """
                    ),
                    {
                        "tenant": tenant_id,
                        "store": store_id,
                        "product": product_id,
                        "external": f"gid://shopify/Product/{product_id.hex[:8]}",
                    },
                )
    engine.dispose()
    print("seeded")


if __name__ == "__main__":
    main()
