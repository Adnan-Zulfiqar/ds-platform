/**
 * UX-L2C-R3 — Shopify listing load/error truthfulness and retry UX.
 * Hermetic mocked routes only.
 */
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";

import {
  buildSyntheticProduct,
  DEMO_PRODUCT_ID,
  mockAuthResponse,
  openMockedEditor,
} from "./helpers/editor-fixture";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import { captureEvidenceScreenshot } from "./helpers/screenshot-evidence";
import type { StoreListing } from "@/types/api";

const UX_L2C_R3_SHOT_ROOT = resolveSuiteShotRoot("l2c-r3-after", [
  "UX_L2C_R3_SHOT_ROOT",
  "UX_L2C_SHOT_ROOT",
]);

function syncedListing(overrides: Partial<StoreListing> = {}): StoreListing {
  return {
    id: "66666666-6666-4666-8666-666666666666",
    storeId: "55555555-5555-4555-8555-555555555555",
    productId: DEMO_PRODUCT_ID,
    externalProductId: "1001",
    externalHandle: "lamp",
    externalGraphqlId: null,
    shopDomain: "demo.myshopify.com",
    storefrontUrl: null,
    adminUrl: "https://demo.myshopify.com/admin/products/1001",
    onlineStorePublished: false,
    status: "synced",
    lastSyncedAt: new Date().toISOString(),
    lastError: null,
    publishedAt: null,
    lastFailedSyncAt: null,
    ...overrides,
  };
}

async function shot(page: Page, name: string) {
  return captureEvidenceScreenshot(page, name, { root: UX_L2C_R3_SHOT_ROOT });
}

async function openProductDetailWithListings(
  page: Page,
  options: {
    listingsStatus?: number;
    listingsBody?: StoreListing[];
    listingsDelayMs?: number;
    listingsAttempts?: Array<{ status: number; body?: StoreListing[] }>;
  } = {},
) {
  const product = buildSyntheticProduct();
  const auth = mockAuthResponse();
  let attempt = 0;

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
  await page.route(`**/api/v1/products/${DEMO_PRODUCT_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(product),
    }),
  );
  await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}/listings`, async (route) => {
    if (options.listingsDelayMs && options.listingsDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, options.listingsDelayMs));
    }
    attempt += 1;
    const scripted = options.listingsAttempts?.[attempt - 1];
    const status = scripted?.status ?? options.listingsStatus ?? 200;
    const body =
      status >= 200 && status < 300
        ? JSON.stringify(scripted?.body ?? options.listingsBody ?? [])
        : JSON.stringify({
            code: "internal_error",
            message: "Could not load listings.",
            details: [],
            requestId: "req-ux-l2c-r3",
          });
    return route.fulfill({
      status,
      contentType: "application/json",
      body,
    });
  });
  await page.goto(`/products/${DEMO_PRODUCT_ID}`);
  return product;
}

test.describe("UX-L2C-R3 listings status truth", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("delayed listings shows checking and never Draft", async ({ page }) => {
    await openMockedEditor(page, { listingsDelayMs: 8_000, listingsResponse: [] });
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Checking Shopify status…",
    );
    await expect(page.getByText("Draft — not on Shopify")).toHaveCount(0);
    await shot(page, "r3-1440-checking");
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Draft — not on Shopify",
      { timeout: 15_000 },
    );
  });

  test("HTTP 500 shows unavailable with retry", async ({ page }) => {
    await openMockedEditor(page, { listingsHttpStatus: 500 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Shopify status unavailable",
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("shopify-status-retry")).toBeVisible();
    await shot(page, "r3-1440-unavailable");
  });

  test("network failure shows unavailable without raw error output", async ({ page }) => {
    const product = buildSyntheticProduct();
    await page.route("**/api/v1/auth/refresh", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockAuthResponse()),
      }),
    );
    await page.route("**/api/v1/auth/me", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockAuthResponse().identity),
      }),
    );
    await page.route(`**/api/v1/drafts/${product.id}**`, async (route) => {
      const url = route.request().url();
      if (url.includes("/listings")) {
        return route.abort("failed");
      }
      if (route.request().method() === "GET" && !url.includes("/seo-score")) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(product),
        });
      }
      return route.continue();
    });
    await page.goto(`/drafts/${product.id}`);
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Shopify status unavailable",
      { timeout: 15_000 },
    );
    await expect(page.getByText(/internal_error|req-ux/i)).toHaveCount(0);
  });

  test("retry to empty list shows Draft", async ({ page }) => {
    await openMockedEditor(page, {
      listingsAttemptResponder: (attempt) =>
        attempt === 1 ? { status: 500 } : { status: 200, body: [] },
    });
    await expect(page.getByTestId("shopify-status-retry")).toBeVisible();
    await page.getByTestId("shopify-status-retry").click();
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Draft — not on Shopify",
      { timeout: 15_000 },
    );
  });

  test("retry to synced listing shows Added to Shopify", async ({ page }) => {
    const syncedAt = "2026-09-12T10:00:00.000Z";
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ updatedAt: syncedAt }),
      listingsAttemptResponder: (attempt) =>
        attempt === 1
          ? { status: 503 }
          : {
              status: 200,
              body: [syncedListing({ onlineStorePublished: null, lastSyncedAt: syncedAt })],
            },
    });
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Shopify status unavailable",
      { timeout: 15_000 },
    );
    await page.getByTestId("shopify-status-retry").click();
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify", {
      timeout: 15_000,
    });
    await shot(page, "r3-1440-retry-added-to-shopify");
  });

  test("retry with visibility true shows Visible on your shop", async ({ page }) => {
    const syncedAt = "2026-09-12T10:00:00.000Z";
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ updatedAt: syncedAt }),
      listingsAttemptResponder: (attempt) =>
        attempt === 1
          ? { status: 500 }
          : {
              status: 200,
              body: [
                syncedListing({
                  onlineStorePublished: true,
                  lastSyncedAt: syncedAt,
                  storefrontUrl: "https://demo.myshopify.com/products/lamp",
                }),
              ],
            },
    });
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Shopify status unavailable",
      { timeout: 15_000 },
    );
    await page.getByTestId("shopify-status-retry").click();
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Visible on your shop",
      { timeout: 15_000 },
    );
    await shot(page, "r3-1440-retry-visible-on-shop");
  });

  test("cached synced listing plus refresh failure preserves status with warning", async ({
    page,
  }) => {
    const syncedAt = "2026-09-12T10:00:00.000Z";
    const product = buildSyntheticProduct({ updatedAt: syncedAt });
    const listing = syncedListing({ onlineStorePublished: null, lastSyncedAt: syncedAt });
    await openMockedEditor(page, {
      product,
      listingsAttemptResponder: (attempt) =>
        attempt === 1 ? { status: 200, body: [listing] } : { status: 503 },
    });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify");
    await page.route("**/api/v1/drafts?**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: product.id,
              title: product.title,
              externalId: product.externalId,
              status: "draft",
              variantCount: product.variantCount,
              sellPrice: product.sellPrice,
              stockQuantity: product.stockQuantity,
              supplierName: product.supplierName,
              aiStatus: product.aiStatus,
              updatedAt: product.updatedAt,
            },
          ],
          meta: {
            page: 1,
            size: 20,
            totalItems: 1,
            totalPages: 1,
            hasNext: false,
            hasPrevious: false,
          },
        }),
      }),
    );
    await page.getByRole("link", { name: /Back to drafts/i }).click();
    await expect(page).toHaveURL(/\/drafts$/);
    await page.getByTestId("draft-title-link").click();
    await expect(page).toHaveURL(new RegExp(`/drafts/${DEMO_PRODUCT_ID}$`));
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("shopify-status-note")).toContainText(
      "Couldn't refresh Shopify status",
      { timeout: 15_000 },
    );
    await shot(page, "r3-1440-cached-refresh-failed");
  });

  test("product detail follows the same unavailable rules", async ({ page }) => {
    await openProductDetailWithListings(page, { listingsStatus: 503 });
    await expect(page.getByTestId("published-product-lifecycle")).toHaveText(
      "Shopify status unavailable",
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("shopify-status-retry")).toBeVisible();
  });

  test("products list shows conservative Added to Shopify status", async ({ page }) => {
    const auth = mockAuthResponse();
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
    await page.route("**/api/v1/products?**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              ...buildSyntheticProduct(),
              status: "published",
            },
          ],
          meta: {
            page: 1,
            size: 20,
            totalItems: 1,
            totalPages: 1,
            hasNext: false,
            hasPrevious: false,
          },
        }),
      }),
    );
    await page.goto("/products");
    await expect(page.getByTestId("product-listing-status")).toHaveText("Added to Shopify");
    await expect(page.getByText(/Up to date on Shopify/i)).toHaveCount(0);
    await shot(page, "r3-1440-products-list-conservative");
  });
});

test.describe("UX-L2C-R3 responsive and a11y", () => {
  test("1024 dark unavailable with retry", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.emulateMedia({ colorScheme: "dark" });
    await openMockedEditor(page, { listingsHttpStatus: 500 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Shopify status unavailable",
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("shopify-status-retry")).toBeVisible();
    await shot(page, "r3-1024-dark-unavailable-retry");
  });

  test("390 mobile unavailable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openMockedEditor(page, { listingsHttpStatus: 500 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Shopify status unavailable",
      { timeout: 15_000 },
    );
    await shot(page, "r3-390-unavailable");
  });

  test("320 mobile retry target is at least 44x44 and no overflow", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await openMockedEditor(page, { listingsHttpStatus: 500 });
    const retry = page.getByTestId("shopify-status-retry");
    await expect(retry).toBeVisible({ timeout: 15_000 });
    const box = await retry.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflow).toBe(false);
    await shot(page, "r3-320-retry-loading");
  });

  test("keyboard focus and live region announce status", async ({ page }) => {
    await openMockedEditor(page, { listingsHttpStatus: 500 });
    const retry = page.getByTestId("shopify-status-retry");
    await expect(retry).toBeVisible({ timeout: 15_000 });
    await retry.focus();
    await expect(retry).toBeFocused();
    await expect(page.getByTestId("shopify-listing-status")).toContainText(
      "Shopify status unavailable",
    );
  });
});

test.describe("UX-L2C-R3 evidence non-empty", () => {
  test("shot root exists and is repo-relative by default", () => {
    expect(UX_L2C_R3_SHOT_ROOT.includes("test-results")).toBe(true);
    fs.mkdirSync(UX_L2C_R3_SHOT_ROOT, { recursive: true });
    expect(fs.existsSync(UX_L2C_R3_SHOT_ROOT)).toBe(true);
  });
});
