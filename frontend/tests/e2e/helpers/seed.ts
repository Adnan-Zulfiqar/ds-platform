import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

/**
 * Seed draft products directly into the test database.
 *
 * Products only enter this system through the AliExpress import, which needs
 * supplier credentials and a mocked transport — neither of which a browser
 * test can provide. The alternative would be a "create a product" endpoint
 * that exists only for tests, and production API surface built for tests is
 * surface an attacker gets too.
 *
 * So the seeding happens here, in test code, by shelling out to the backend's
 * own interpreter. It writes the same rows an import would leave behind; every
 * assertion afterwards goes through the real API.
 *
 * Skips the whole file cleanly when the interpreter or database is not
 * configured, rather than failing with a wall of spawn errors.
 */

const PYTHON =
  process.env.E2E_PYTHON ?? "C:/dspm3av2/backend/.venv/Scripts/python.exe";
const DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  "postgresql+psycopg://droppilot:droppilot@localhost:5432/droppilot_m3a4b";

const SCRIPT = `
import sys, uuid
import sqlalchemy as sa

url, tenant_id, count, published, flagged, prefix = sys.argv[1:7]
count, published, flagged = int(count), int(published), int(flagged)

engine = sa.create_engine(url)
with engine.begin() as connection:
    for index in range(count):
        # A "flagged" draft is one the supplier gave no freight cost for --
        # the real, common case -- not an artificial flag.
        no_freight = index < flagged
        product_id = uuid.uuid4()
        connection.execute(
            sa.text("""
                INSERT INTO products
                    (id, tenant_id, source, external_id, title, status,
                     cost_price_min, shipping_cost, currency, sell_price,
                     stock_quantity, needs_review, pricing_review_reasons,
                     created_at, updated_at)
                VALUES
                    (:id, :tenant, 'manual', :external, :title, 'draft',
                     10.00, :shipping, 'GBP', 30.00, 25, false, '[]'::jsonb,
                     now(), now())
            """),
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
                sa.text("""
                    INSERT INTO stores
                        (id, tenant_id, name, slug, platform, status, currency,
                         timezone, settings, inventory_sync_enabled,
                         pricing_sync_enabled, order_sync_enabled, health_score,
                         created_at, updated_at)
                    VALUES
                        (:id, :tenant, :name, :slug, 'shopify', 'connected', 'GBP',
                         'UTC', '{}'::jsonb, true, true, true, 100, now(), now())
                """),
                {
                    "id": store_id,
                    "tenant": tenant_id,
                    "name": f"Seed store {store_id.hex[:6]}",
                    "slug": f"seed-{store_id.hex[:10]}",
                },
            )
            connection.execute(
                sa.text("""
                    INSERT INTO store_listings
                        (id, tenant_id, store_id, product_id, external_product_id,
                         external_variant_map, inventory_item_map, status,
                         created_at, updated_at)
                    VALUES
                        (gen_random_uuid(), :tenant, :store, :product, :external,
                         '{}'::jsonb, '{}'::jsonb, 'synced', now(), now())
                """),
                {
                    "tenant": tenant_id,
                    "store": store_id,
                    "product": product_id,
                    "external": f"gid://shopify/Product/{product_id.hex[:8]}",
                },
            )
engine.dispose()
print("seeded")
`;

export interface SeedOptions {
  published?: number;
  flagged?: number;
  prefix?: string;
}

export async function seedDrafts(
  tenantId: string,
  count: number,
  options: SeedOptions = {},
): Promise<void> {
  const { stdout } = await run(PYTHON, [
    "-c",
    SCRIPT,
    DATABASE_URL,
    tenantId,
    String(count),
    String(options.published ?? 0),
    String(options.flagged ?? 0),
    options.prefix ?? "Impact draft",
  ]);
  if (!stdout.includes("seeded")) {
    throw new Error(`Seeding did not confirm: ${stdout}`);
  }
}

/** Whether seeding can run at all, so the suite skips instead of erroring. */
export async function canSeed(): Promise<boolean> {
  try {
    await run(PYTHON, ["-c", "import sqlalchemy"]);
    return true;
  } catch {
    return false;
  }
}
