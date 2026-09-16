import type { Page } from "@playwright/test";

import type {
  AuthResponse,
  ProductDetail,
  SeoScore,
  ShopifyPublishResult,
  StoreListing,
} from "@/types/api";

import { resolveSuiteShotRoot } from "./evidence-paths";
import { blockGoogleIdentityScript } from "./auth";

/** Stable synthetic draft id — never a production customer product. */
export const DEMO_PRODUCT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

/** Outside the Git worktree — screenshots must not land in the repo. */
export const UX_L2A_SHOT_ROOT = resolveSuiteShotRoot("ux-l2a-editor-foundation", [
  "UX_L2A_SHOT_ROOT",
]);

export function mockAuthResponse(): AuthResponse {
  const now = Date.now();
  return {
    identity: {
      user: {
        id: "11111111-1111-4111-8111-111111111111",
        tenantId: "22222222-2222-4222-8222-222222222222",
        email: "ux-l2a-demo@example.com",
        firstName: "Demo",
        lastName: "Seller",
        fullName: "Demo Seller",
        isActive: true,
        isVerified: true,
        lastLoginAt: new Date(now).toISOString(),
        createdAt: new Date(now).toISOString(),
        updatedAt: new Date(now).toISOString(),
      },
      tenant: {
        id: "22222222-2222-4222-8222-222222222222",
        name: "Demo Workspace",
        slug: "demo-workspace",
        status: "active",
        timezone: "Europe/London",
        defaultCurrency: "GBP",
      },
      roles: ["owner"],
    },
    tokens: {
      accessToken: "ux-l2a-demo-access-token",
      tokenType: "bearer",
      expiresIn: 3600,
      refreshToken: null,
      accessExpiresAt: new Date(now + 3_600_000).toISOString(),
      refreshExpiresAt: new Date(now + 86_400_000).toISOString(),
    },
  };
}

/** Tiny PNG (1×1) as a data URL — allowed by production img-src without CDN calls. */
export const DEMO_IMAGE_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

export function buildSyntheticProduct(
  overrides: Partial<ProductDetail> = {},
): ProductDetail {
  const now = new Date().toISOString();
  const base: ProductDetail = {
    id: DEMO_PRODUCT_ID,
    source: "aliexpress",
    externalId: "1005000000000000",
    externalUrl: "https://www.aliexpress.com/item/1005000000000000.html",
    title: "Wireless Desk Lamp with USB Charging",
    variantCount: 1,
    categoryId: null,
    categoryName: null,
    brand: null,
    status: "draft",
    currency: "GBP",
    costPriceMin: "8.50",
    costPriceMax: "8.50",
    sellPrice: "19.99",
    stockQuantity: 120,
    packageWeightKg: "0.45",
    packageLengthCm: 20,
    packageWidthCm: 12,
    packageHeightCm: 8,
    deliveryTimeDays: 12,
    shipToCountry: "GB",
    shippingCost: "3.20",
    warehouseOrigin: "CN",
    importShipToCountry: "GB",
    importShipToCheckedAt: now,
    requiresShipping: true,
    hsCode: null,
    countryOfOrigin: "CN",
    customsDescription: null,
    handlingTimeDays: 2,
    weightUnit: "kg",
    dimensionUnit: "cm",
    supplierName: "AliExpress",
    rating: "4.7",
    reviewCount: 128,
    orderCount: 540,
    lastSyncedAt: now,
    lastSyncError: null,
    createdAt: now,
    updatedAt: now,
    seoTitle: "Wireless Desk Lamp",
    seoDescription: "A calm desk lamp for home offices.",
    metaKeywords: null,
    searchTopics: [],
    seoPlanning: {},
    ogTitle: null,
    ogDescription: null,
    redirectOldHandle: false,
    slug: "wireless-desk-lamp",
    vendor: null,
    tags: [],
    aiStatus: "not_optimized",
    aiLastGeneratedAt: null,
    aiProvider: null,
    aiVersion: null,
    optimizedTitle: null,
    optimizedDescription: null,
    variants: [
      {
        id: "33333333-3333-4333-8333-333333333333",
        externalVariantId: "v1",
        externalAttributes: null,
        label: "Default",
        costPrice: "8.50",
        listPrice: "19.99",
        currency: "GBP",
        stockQuantity: 120,
        imageUrl: null,
        merchantSku: "LAMP-01",
        sellPrice: "19.99",
        compareAtPrice: null,
        isEnabled: true,
      },
    ],
    images: [
      {
        id: "44444444-4444-4444-8444-444444444444",
        url: DEMO_IMAGE_DATA_URL,
        position: 0,
        altText: "Desk lamp",
        isSupplier: true,
      },
    ],
    description: "<p>A calm wireless desk lamp for home offices.</p>",
    supplierDescription: "<p>Supplier description</p>",
    supplierTitle: "Wireless Desk Lamp",
    supplierBrand: null,
  };
  return { ...base, ...overrides };
}

export function emptyListings(): StoreListing[] {
  return [];
}

/**
 * A synced listing for the demo draft, with every URL on the trusted host.
 *
 * `lastSyncedAt` defaults to a minute from now so it is later than the
 * `updatedAt` of any `buildSyntheticProduct()` created in the same test —
 * the default pairing reads as "on Shopify, nothing newer saved". Tests
 * that want "changes not sent" pass explicit timestamps.
 */
export function syncedDemoListing(overrides: Partial<StoreListing> = {}): StoreListing {
  const now = new Date(Date.now() + 60_000).toISOString();
  return {
    id: "66666666-6666-4666-8666-666666666666",
    storeId: DEMO_STORE_ID,
    productId: DEMO_PRODUCT_ID,
    externalProductId: "8123456789",
    externalHandle: "wireless-desk-lamp",
    externalGraphqlId: "gid://shopify/Product/8123456789",
    shopDomain: "demo-shop.myshopify.com",
    storefrontUrl: "https://demo-shop.myshopify.com/products/wireless-desk-lamp",
    adminUrl: "https://demo-shop.myshopify.com/admin/products/8123456789",
    onlineStorePublished: null,
    status: "synced",
    lastSyncedAt: now,
    lastError: null,
    publishedAt: null,
    lastFailedSyncAt: null,
    ...overrides,
  };
}

/** The publish response the backend returns for the demo draft. */
export function demoPublishResult(overrides: Partial<ShopifyPublishResult> = {}): ShopifyPublishResult {
  return {
    message: "Published.",
    listingId: "66666666-6666-4666-8666-666666666666",
    externalProductId: "8123456789",
    externalHandle: "wireless-desk-lamp",
    externalGraphqlId: "gid://shopify/Product/8123456789",
    shopDomain: "demo-shop.myshopify.com",
    storefrontUrl: null,
    adminUrl: "https://demo-shop.myshopify.com/admin/products/8123456789",
    onlineStorePublished: null,
    updated: true,
    ...overrides,
  };
}

export const DEMO_STORE_ID = "55555555-5555-4555-8555-555555555555";

export function mockShopifyStoresResponse() {
  const now = new Date().toISOString();
  return {
    items: [
      {
        id: DEMO_STORE_ID,
        name: "Demo Shopify",
        slug: "demo-shopify",
        platform: "shopify",
        status: "connected",
        storefrontUrl: "https://demo.myshopify.com",
        externalStoreId: "demo",
        currency: "GBP",
        currencyLastSyncedAt: now,
        timezone: "Europe/London",
        settings: {},
        inventorySyncEnabled: true,
        pricingSyncEnabled: true,
        orderSyncEnabled: true,
        lastSyncAt: null,
        lastActivityAt: null,
        lastError: null,
        healthScore: 100,
        createdAt: now,
        updatedAt: now,
      },
    ],
    meta: {
      page: 1,
      size: 50,
      totalItems: 1,
      totalPages: 1,
      hasNext: false,
      hasPrevious: false,
    },
  };
}

export function mockPublishReadiness(overrides: Record<string, unknown> = {}) {
  const now = new Date().toISOString();
  return {
    channel: "shopify",
    storeId: DEMO_STORE_ID,
    draftId: DEMO_PRODUCT_ID,
    draftUpdatedAt: now,
    canPublish: true,
    blockers: [],
    recommendations: [],
    checkedAt: now,
    ...overrides,
  };
}


export function demoSeoScore(score = 72): SeoScore {
  return {
    score,
    status: "good",
    sections: {},
    warnings: [],
    explanations: [],
    metaKeywordsExported: false,
    note: "",
  };
}

/**
 * Sign the SPA in via a mocked refresh response and serve a synthetic draft.
 *
 * No production customer data and no real AliExpress / Shopify / eBay calls.
 */
export async function openMockedEditor(
  page: Page,
  options: {
    product?: ProductDetail;
    listings?: StoreListing[];
    seoScore?: SeoScore;
    patchStatus?: number;
    patchBody?: unknown;
    /** Artificial network delay for PATCH only — never used to invent UI state. */
    patchDelayMs?: number;
    /** Per-attempt PATCH override (1-based). Falls back to patchStatus/patchBody. */
    patchResponder?: (attempt: number) => { status: number; body?: unknown };
    /**
     * Listings variants (UX-L2D-05, selectively adapted from the historical
     * fixture): hold the response, fail it, or answer per attempt (1-based)
     * so status, retry and refresh-failure states can be reached honestly.
     */
    listingsDelayMs?: number;
    listingsHttpStatus?: number;
    listingsResponder?: (attempt: number) => { status: number; body?: StoreListing[] } | null;
    /**
     * Answer `POST /integrations/shopify/publish`. On a 2xx the served
     * listings become the publish result's listing from then on — the way
     * the real backend persists `StoreListing` before responding — so the
     * refetch the editor triggers sees what the publish reported. When
     * omitted the endpoint is not mocked (tests may route it themselves).
     */
    publishResponder?: (attempt: number) => { status: number; body?: unknown; delayMs?: number };
    /**
     * Also answer the Products side of the journey (UX-L2D-07):
     * `GET /products/{id}` with the draft, and `GET /products` with the draft
     * once its served listing is synced — the same predicate the backend's
     * Products list applies — so a publish can be followed to the product
     * page and the Products list without a backend.
     */
    productsResponder?: boolean;
  } = {},
): Promise<ProductDetail> {
  const product = options.product ?? buildSyntheticProduct();
  const listings = options.listings ?? emptyListings();
  const seoScore = options.seoScore ?? demoSeoScore();
  const auth = mockAuthResponse();
  let patchAttempts = 0;
  let listingsAttempts = 0;
  let publishAttempts = 0;
  // Mutable so a mocked publish can move the "server" to a synced state.
  let servedListings = listings;

  await blockGoogleIdentityScript(page);

  await page.route("**/api/v1/auth/refresh", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(auth),
    }),
  );
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(auth.identity),
    }),
  );
  await page.route("**/api/v1/auth/logout", (route) =>
    route.fulfill({ status: 204, body: "" }),
  );

  // The three requests the application shell makes on every protected page,
  // whichever page it is: the sidebar's workspace counts (the desktop rail is
  // mounted at every viewport, merely hidden below `md`) and the bell menu's
  // unread count and first page. Without a backend they are refused, and the
  // refusal lands in the console as `net::ERR_FAILED`, which the
  // console-clean assertions in the editor suites rightly treat as an error.
  // Answered exactly — by pathname, not by prefix — so nothing else the
  // editor does is intercepted by accident.
  await page.route(
    (url) => url.pathname.endsWith("/api/v1/products/workspace-counts"),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ drafts: 1, products: 0 }),
      }),
  );
  await page.route(
    (url) => url.pathname.endsWith("/api/v1/notifications/unread-count"),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ unread: 0 }),
      }),
  );
  await page.route(
    (url) => url.pathname.endsWith("/api/v1/notifications"),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [],
          meta: {
            page: 1,
            size: 8,
            totalItems: 0,
            totalPages: 0,
            hasNext: false,
            hasPrevious: false,
          },
        }),
      }),
  );

  await page.route("**/api/v1/stores**", async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(mockShopifyStoresResponse()),
    });
  });

  await page.route("**/api/v1/integrations/shopify/publish-readiness", async (route) => {
    const postData = route.request().postDataJSON() as {
      productId?: string;
      storeId?: string | null;
      expectedUpdatedAt?: string | null;
    } | null;
    const draftUpdatedAt =
      postData?.expectedUpdatedAt ?? product.updatedAt ?? new Date().toISOString();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        mockPublishReadiness({
          storeId: postData?.storeId ?? null,
          draftId: product.id,
          draftUpdatedAt,
          canPublish: Boolean(postData?.storeId),
          blockers: postData?.storeId
            ? []
            : [
                {
                  code: "store_required",
                  message: "Select where you want to publish this product.",
                  field: "storeId",
                  section: "publishing",
                  action: "Choose a store",
                },
              ],
        }),
      ),
    });
  });

  // One handler: Playwright matches last-registered first, so branching here
  // avoids listings/seo-score being swallowed by a broad drafts pattern.
  await page.route(`**/api/v1/drafts/${product.id}**`, async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    if (url.includes("/listings")) {
      if (options.listingsDelayMs && options.listingsDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, options.listingsDelayMs));
      }
      listingsAttempts += 1;
      const responded = options.listingsResponder?.(listingsAttempts);
      const status = responded?.status ?? options.listingsHttpStatus ?? 200;
      const ok = status >= 200 && status < 300;
      return route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(
          ok
            ? (responded?.body ?? servedListings)
            : {
                code: "internal_error",
                message: "Could not load listings.",
                details: [],
                requestId: "req-ux-l2d-05",
              },
        ),
      });
    }
    if (url.includes("/seo-score")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(seoScore),
      });
    }
    if (url.includes("/refresh")) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(product),
      });
    }
    if (method === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(product),
      });
    }
    if (method === "PATCH") {
      if (options.patchDelayMs && options.patchDelayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, options.patchDelayMs));
      }
      patchAttempts += 1;
      const responded = options.patchResponder?.(patchAttempts);
      const status = responded?.status ?? options.patchStatus ?? 200;
      const body =
        responded?.body ??
        options.patchBody ??
        (status >= 400
          ? {
              code: status === 409 ? "conflict" : "internal_error",
              message:
                status === 409
                  ? "Someone else saved this product."
                  : "Could not save.",
              details: [],
              requestId: "req-ux-l2a-demo",
            }
          : { ...product, updatedAt: new Date().toISOString() });
      return route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(product),
    });
  });

  if (options.productsResponder) {
    await page.route((url) => /\/api\/v1\/products(\?.*)?$/.test(url.pathname + url.search) && !url.pathname.endsWith("/workspace-counts"), async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      const published = servedListings.some((row) => row.status === "synced");
      const items = published
        ? [(() => {
            // A list row is the detail minus its nested collections.
            // eslint-disable-next-line @typescript-eslint/no-unused-vars
            const { variants, images, description, supplierDescription, supplierTitle, supplierBrand, ...row } = product;
            return row;
          })()]
        : [];
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items,
          meta: { page: 1, size: 25, totalItems: items.length, totalPages: 1, hasNext: false, hasPrevious: false },
        }),
      });
    });
    await page.route((url) => url.pathname.endsWith(`/api/v1/products/${product.id}`), async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(product) });
    });
  }

  if (options.publishResponder) {
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishAttempts += 1;
      const responded = options.publishResponder!(publishAttempts);
      if (responded.delayMs && responded.delayMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, responded.delayMs));
      }
      const ok = responded.status >= 200 && responded.status < 300;
      if (ok) {
        const result = (responded.body ?? demoPublishResult()) as ShopifyPublishResult;
        servedListings = [
          syncedDemoListing({
            id: result.listingId,
            externalProductId: result.externalProductId,
            externalHandle: result.externalHandle,
            externalGraphqlId: result.externalGraphqlId,
            shopDomain: result.shopDomain,
            storefrontUrl: result.storefrontUrl,
            adminUrl: result.adminUrl,
            onlineStorePublished: result.onlineStorePublished,
            lastSyncedAt: new Date().toISOString(),
          }),
        ];
      }
      return route.fulfill({
        status: responded.status,
        contentType: "application/json",
        body: JSON.stringify(
          responded.body ??
            (ok
              ? demoPublishResult()
              : {
                  code: "publish_failed",
                  message: "Shopify rejected the product.",
                  details: [],
                  requestId: "req-ux-l2d-05",
                }),
        ),
      });
    });
  }

  await page.goto(`/drafts/${product.id}`);
  return product;
}
