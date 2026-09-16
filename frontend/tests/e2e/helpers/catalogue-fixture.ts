import type { Locator, Page, Route } from "@playwright/test";

import type { Page as ApiPage, Product, ProductDetail, StoreListing } from "@/types/api";

import { buildSyntheticProduct, mockAuthResponse } from "./editor-fixture";

/**
 * Backend-less catalogue (UX-L2D-04).
 *
 * A small in-memory "server" that answers the list endpoints the way the
 * real one does — `q` matched against title, external id and supplier
 * name; `sort_by`/`sort_dir` honoured only in the wire names the backend
 * reads; `page`/`size` with the same `meta` shape — so search, sort and
 * pagination are exercised against behaviour rather than canned pages.
 * Product detail and listings are answered per id; unknown ids get 404
 * exactly as the API does for a missing or foreign-tenant row. Every
 * request is logged so tests can assert what the page asked for, and what
 * it did not (no listings request per table row).
 */

export interface CatalogueLog {
  requests: { method: string; path: string; search: string }[];
}

export interface CatalogueWorld {
  drafts: ProductDetail[];
  products: ProductDetail[];
  /** Listings per product id; missing → `[]`. */
  listings: Record<string, StoreListing[]>;
  /** Endpoint keys answering 500. */
  fail: Set<"drafts" | "products" | "listings" | "product">;
}

const now = Date.now();
const iso = (offsetMs = 0) => new Date(now + offsetMs).toISOString();

/**
 * The catalogue renders every item twice — a table row from Tailwind's `lg`
 * (1024px) and a card below it — and CSS decides which one shows. Each has
 * its own test id (`draft-row`/`draft-card`, `product-row`/`product-card`),
 * so a test asks for the representation its viewport renders instead of
 * filtering by visibility, and a count of 1 means one item, not one item
 * drawn twice.
 */
export const CATALOGUE_CARD_BREAKPOINT = 1024;

export type CatalogueKind = "draft" | "product";

export function catalogueRows(page: Page, kind: CatalogueKind): Locator {
  const width = page.viewportSize()?.width ?? CATALOGUE_CARD_BREAKPOINT;
  return page.getByTestId(width >= CATALOGUE_CARD_BREAKPOINT ? `${kind}-row` : `${kind}-card`);
}

export const PUBLISHED_ID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000901";
export const PUBLISHED_HIDDEN_ID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000902";
export const DRAFT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000001";
export const MISSING_ID = "aaaaaaaa-bbbb-4ccc-8ddd-ffffffffffff";

const TITLES = [
  "Wireless Desk Lamp with USB Charging",
  "Foldable Laptop Stand — Aluminium",
  "Magnetic Phone Mount for Car Vent",
  "Insulated Stainless Steel Water Bottle 750ml",
  "Bluetooth Sleep Headband",
  "Pet Grooming Glove (Pair)",
  "LED Strip Light 5m RGB",
  "Portable Blender 380ml",
  "Silicone Kitchen Utensil Set",
  "Resistance Bands, 5 Levels",
];

export function syncedListing(productId: string, overrides: Partial<StoreListing> = {}): StoreListing {
  return {
    id: `listing-${productId.slice(-4)}`,
    storeId: "s1",
    productId,
    externalProductId: "8123456789",
    externalHandle: "wireless-desk-lamp",
    externalGraphqlId: "gid://shopify/Product/8123456789",
    shopDomain: "demo-shop.myshopify.com",
    storefrontUrl: "https://demo-shop.myshopify.com/products/wireless-desk-lamp",
    // The backend builds `https://{shop_domain}/admin/products/{id}`.
    adminUrl: "https://demo-shop.myshopify.com/admin/products/8123456789",
    onlineStorePublished: true,
    status: "synced",
    lastSyncedAt: iso(-3_600_000),
    lastError: null,
    publishedAt: iso(-86_400_000),
    lastFailedSyncAt: null,
    ...overrides,
  };
}

/** Sixty drafts so pagination has three pages, plus two published products. */
export function catalogueWorld(): CatalogueWorld {
  const drafts: ProductDetail[] = Array.from({ length: 60 }, (_, i) => {
    const n = i + 1;
    return buildSyntheticProduct({
      id: `aaaaaaaa-bbbb-4ccc-8ddd-${String(n).padStart(12, "0")}`,
      title: `${TITLES[i % TITLES.length]} #${n}`,
      externalId: `100500000000${String(n).padStart(4, "0")}`,
      supplierName: i % 3 === 0 ? "BrightHome Ltd" : "AliExpress",
      costPriceMin: (5 + (i % 17)).toFixed(2),
      costPriceMax: (5 + (i % 17) + 2).toFixed(2),
      stockQuantity: (i * 37) % 500,
      aiStatus: i % 5 === 0 ? "failed" : i % 2 === 0 ? "optimized" : "not_optimized",
      status: i === 7 ? "unavailable" : "draft",
      createdAt: iso(-86_400_000 * (60 - i)),
      updatedAt: iso(-3_600_000 * (60 - i)),
    });
  });
  const products: ProductDetail[] = [
    buildSyntheticProduct({
      id: PUBLISHED_ID,
      title: "Ceramic Pour-Over Coffee Set",
      externalId: "1005000000009901",
      status: "draft",
      sellPrice: "24.99",
      currency: "GBP",
      updatedAt: iso(-7_200_000),
      description: "<p>Hand-glazed <b>ceramic</b> set.</p><script>alert(1)</script>",
    }),
    buildSyntheticProduct({
      id: PUBLISHED_HIDDEN_ID,
      title: "Bamboo Desk Organiser",
      externalId: "1005000000009902",
      status: "draft",
      sellPrice: null,
      stockQuantity: 0,
      updatedAt: iso(-600_000),
    }),
  ];
  return {
    drafts,
    products,
    listings: {
      [PUBLISHED_ID]: [syncedListing(PUBLISHED_ID)],
      [PUBLISHED_HIDDEN_ID]: [
        syncedListing(PUBLISHED_HIDDEN_ID, {
          onlineStorePublished: false,
          storefrontUrl: "http://demo-shop.myshopify.com/products/bamboo", // not https → never linked
          adminUrl: "https://evil.example.com/admin", // not a Shopify host → never linked
          lastSyncedAt: iso(-86_400_000),
        }),
      ],
    },
    fail: new Set(),
  };
}

function toRow(p: ProductDetail): Product {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { variants, images, description, supplierDescription, supplierTitle, supplierBrand, ...row } = p;
  return row;
}

function listPage(items: ProductDetail[], search: URLSearchParams): ApiPage<Product> {
  const q = (search.get("q") ?? "").toLowerCase();
  const sortBy = search.get("sort_by") ?? "created_at";
  const sortDir = search.get("sort_dir") ?? "desc";
  const page = Number.parseInt(search.get("page") ?? "1", 10) || 1;
  const size = Number.parseInt(search.get("size") ?? "25", 10) || 25;

  let rows = items;
  if (q) {
    rows = rows.filter(
      (p) =>
        p.title.toLowerCase().includes(q) ||
        p.externalId.toLowerCase().includes(q) ||
        (p.supplierName ?? "").toLowerCase().includes(q),
    );
  }
  const key: Record<string, (p: ProductDetail) => string | number> = {
    created_at: (p) => p.createdAt,
    updated_at: (p) => p.updatedAt,
    title: (p) => p.title.toLowerCase(),
    status: (p) => p.status,
    cost_price_min: (p) => Number(p.costPriceMin ?? 0),
    sell_price: (p) => Number(p.sellPrice ?? 0),
    stock_quantity: (p) => p.stockQuantity,
    last_synced_at: (p) => p.lastSyncedAt ?? "",
  };
  const byCreated = (p: ProductDetail): string | number => p.createdAt;
  const pick = key[sortBy] ?? byCreated;
  rows = [...rows].sort((a, b) => {
    const av = pick(a);
    const bv = pick(b);
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return sortDir === "asc" ? cmp : -cmp;
  });
  const totalItems = rows.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / size));
  const slice = rows.slice((page - 1) * size, page * size).map(toRow);
  return {
    items: slice,
    meta: { page, size, totalItems, totalPages, hasNext: page < totalPages, hasPrevious: page > 1 },
  };
}

export async function mockCatalogueApi(page: Page, world: CatalogueWorld): Promise<CatalogueLog> {
  const auth = mockAuthResponse();
  const log: CatalogueLog = { requests: [] };

  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: "" }),
  );

  await page.route("**/api/v1/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = request.method();
    log.requests.push({ method, path, search: url.search });
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const fail = () =>
      json({ code: "internal_error", message: "Simulated failure", requestId: "req-catalogue" }, 500);
    const notFound = () =>
      json({ code: "not_found", message: "Product not found.", requestId: "req-catalogue" }, 404);

    if (path === "/auth/refresh") return json(auth);
    if (path === "/auth/me") return json(auth.identity);
    if (path === "/auth/logout") return route.fulfill({ status: 204, body: "" });
    if (path === "/auth/google/nonce") return json({ nonce: "n", expiresInSeconds: 300 });
    if (path === "/products/workspace-counts")
      return json({ drafts: world.drafts.length, products: world.products.length });
    if (path === "/notifications/unread-count") return json({ unread: 0 });
    if (path === "/notifications")
      return json({ items: [], meta: { page: 1, size: 8, totalItems: 0, totalPages: 0, hasNext: false, hasPrevious: false } });
    if (path === "/stores")
      return json({ items: [], meta: { page: 1, size: 25, totalItems: 0, totalPages: 0, hasNext: false, hasPrevious: false } });
    if (path === "/products/import/check") return json({ matches: [] });

    if (path === "/drafts" && method === "GET") {
      if (world.fail.has("drafts")) return fail();
      return json(listPage(world.drafts, url.searchParams));
    }
    if (path === "/products" && method === "GET") {
      if (world.fail.has("products")) return fail();
      return json(listPage(world.products, url.searchParams));
    }

    const detail = path.match(/^\/(drafts|products)\/([^/]+)(\/listings|\/seo-score|\/versions)?$/);
    if (detail && method === "GET") {
      const id = detail[2] ?? "";
      const product = [...world.drafts, ...world.products].find((p) => p.id === id);
      if (detail[3] === "/listings") {
        if (world.fail.has("listings")) return fail();
        if (!product) return notFound();
        return json(world.listings[id] ?? []);
      }
      if (detail[3] === "/seo-score")
        return json({ score: 72, status: "good", sections: {}, warnings: [], explanations: [], metaKeywordsExported: false, note: "" });
      if (detail[3] === "/versions") return json([]);
      if (world.fail.has("product")) return fail();
      if (!product) return notFound();
      return json(product);
    }

    return json({ code: "not_mocked", message: `${method} ${path}` }, 404);
  });

  return log;
}
