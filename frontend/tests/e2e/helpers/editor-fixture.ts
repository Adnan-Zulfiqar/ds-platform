import type { Page } from "@playwright/test";

import type { AuthResponse, ProductDetail, SeoScore, StoreListing } from "@/types/api";

import { blockGoogleIdentityScript } from "./auth";

/** Stable synthetic draft id — never a production customer product. */
export const DEMO_PRODUCT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

/** Outside the Git worktree — screenshots must not land in the repo. */
export const UX_L2A_SHOT_ROOT =
  process.env.UX_L2A_SHOT_ROOT ??
  "C:\\Users\\profe\\DropPilotLogs\\ux-l2a-editor-foundation";

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
  } = {},
): Promise<ProductDetail> {
  const product = options.product ?? buildSyntheticProduct();
  const listings = options.listings ?? emptyListings();
  const seoScore = options.seoScore ?? demoSeoScore();
  const auth = mockAuthResponse();
  let patchAttempts = 0;

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
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(listings),
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

  await page.goto(`/drafts/${product.id}`);
  return product;
}
