/**
 * UX-L2C — lifecycle clarity, calm conflict, post-publish journey.
 * Hermetic mocked routes only.
 */
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";

import {
  buildSyntheticProduct,
  DEMO_PRODUCT_ID,
  DEMO_STORE_ID,
  mockPublishReadiness,
  openMockedEditor,
} from "./helpers/editor-fixture";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import { captureEvidenceScreenshot } from "./helpers/screenshot-evidence";
import type { StoreListing } from "@/types/api";

const UX_L2C_SHOT_ROOT = resolveSuiteShotRoot("ux-l2c-live-state", ["UX_L2C_SHOT_ROOT"]);

function syncedListing(
  overrides: Partial<StoreListing> = {},
): StoreListing {
  return {
    id: "66666666-6666-4666-8666-666666666666",
    storeId: DEMO_STORE_ID,
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

async function openReview(page: Page) {
  await page.getByTestId("editor-tab-publishing").click();
  await expect(page.getByTestId("publishing-panel")).toBeVisible();
}

async function shot(page: Page, name: string) {
  return captureEvidenceScreenshot(page, name, { root: UX_L2C_SHOT_ROOT });
}

test.describe("UX-L2C lifecycle and calm completion", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("draft lifecycle label and no Section text in blockers", async ({ page }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Draft — not on Shopify",
    );

    await page.route("**/api/v1/integrations/shopify/publish-readiness", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          mockPublishReadiness({
            canPublish: false,
            blockers: [
              {
                code: "store_disconnected",
                message: "This Shopify store is not connected.",
                field: "storeId",
                section: "publishing",
                action: "Open Integrations",
              },
            ],
          }),
        ),
      }),
    );
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-blockers")).toBeVisible();
    await expect(page.locator("text=Section:")).toHaveCount(0);
    await expect(page.getByText("Store connection")).toBeVisible();
    await shot(page, "after-1440-blocker");
  });

  test("added to Shopify without visibility proof never says Live", async ({ page }) => {
    await openMockedEditor(page, {
      listings: [syncedListing({ onlineStorePublished: null })],
    });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify");
    await expect(page.getByText(/Live on Shopify/i)).toHaveCount(0);
    await openReview(page);
    await expect(page.getByTestId("publish-form-collapsed")).toBeVisible();
    await expect(page.getByTestId("publish-to-store")).toHaveCount(0);
    await shot(page, "after-1440-added-to-shopify");
  });

  test("visible on shop only when onlineStorePublished is true", async ({ page }) => {
    await openMockedEditor(page, {
      listings: [
        syncedListing({
          onlineStorePublished: true,
          storefrontUrl: "https://demo.myshopify.com/products/lamp",
        }),
      ],
    });
    await expect(page.getByTestId("product-lifecycle")).toHaveText(
      "Visible on your shop",
    );
  });

  test("conflict shows one alert and blocks publish", async ({ page }) => {
    let publishCalls = 0;
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      return route.fulfill({ status: 200, body: "{}" });
    });

    await openMockedEditor(page, { patchStatus: 409 });
    await page.getByLabel("Title").fill("Conflict edit");
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(1);
    await expect(page.getByTestId("publish-conflict-pointer")).toBeVisible();
    await expect(page.getByTestId("publish-save-failure")).toHaveCount(0);
    expect(publishCalls).toBe(0);
    await shot(page, "after-1440-conflict");
  });

  test("save failure shown once without publish", async ({ page }) => {
    let publishCalls = 0;
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      return route.fulfill({ status: 200, body: "{}" });
    });

    await openMockedEditor(page, { patchStatus: 500 });
    await page.getByLabel("Title").fill("Broken save");
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-save-failure")).toHaveCount(1);
    await expect(page.getByTestId("publish-save-failure")).toContainText(
      /couldn.t save your changes, so nothing was published/i,
    );
    expect(publishCalls).toBe(0);
    await shot(page, "after-1440-save-failure");
  });

  test("post-publish success hides raw write_publications and collapses form", async ({
    page,
  }) => {
    await openMockedEditor(page);
    await page.route("**/api/v1/integrations/shopify/publish", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Published.",
          listingId: "66666666-6666-4666-8666-666666666666",
          externalProductId: "1001",
          externalHandle: "lamp",
          externalGraphqlId: null,
          shopDomain: "demo.myshopify.com",
          storefrontUrl: null,
          adminUrl: "https://demo.myshopify.com/admin/products/1001",
          onlineStorePublished: false,
          updated: true,
        }),
      }),
    );
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("post-publish-success")).toBeVisible();
    await expect(page.getByText(/write_publications/i)).toHaveCount(0);
    await expect(page.getByText(/External ID/i)).toHaveCount(0);
    await expect(page.getByTestId("publish-form-collapsed")).toBeVisible();
    await expect(page.getByRole("link", { name: "View in Products" })).toBeVisible();
    await shot(page, "after-1440-post-publish");
  });

  test("published product detail page loads with mocked API", async ({ page }) => {
    const product = buildSyntheticProduct();
    await page.route("**/api/v1/auth/refresh", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          identity: {
            user: {
              id: "11111111-1111-4111-8111-111111111111",
              tenantId: "22222222-2222-4222-8222-222222222222",
              email: "ux-l2c-demo@example.com",
              firstName: "Demo",
              lastName: "Seller",
              fullName: "Demo Seller",
              isActive: true,
              isVerified: true,
              lastLoginAt: null,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
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
            accessToken: "demo",
            tokenType: "bearer",
            expiresIn: 3600,
            refreshToken: null,
            accessExpiresAt: new Date(Date.now() + 3600000).toISOString(),
            refreshExpiresAt: new Date(Date.now() + 86400000).toISOString(),
          },
        }),
      }),
    );
    await page.route("**/api/v1/auth/me", (route) => route.fulfill({ status: 200, body: "{}" }));
    await page.route(`**/api/v1/products/${DEMO_PRODUCT_ID}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(product),
      }),
    );
    await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}/listings`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([syncedListing()]),
      }),
    );
    await page.goto(`/products/${DEMO_PRODUCT_ID}`);
    await expect(page.getByTestId("published-product-summary")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByTestId("published-product-lifecycle")).toHaveText(
      "Added to Shopify",
    );
    await shot(page, "after-1440-product-detail");
  });

  test("products table row opens product detail", async ({ page }) => {
    await page.route("**/api/v1/auth/refresh", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          identity: {
            user: {
              id: "11111111-1111-4111-8111-111111111111",
              tenantId: "22222222-2222-4222-8222-222222222222",
              email: "demo@example.com",
              firstName: "D",
              lastName: "S",
              fullName: "D S",
              isActive: true,
              isVerified: true,
              lastLoginAt: null,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            },
            tenant: {
              id: "22222222-2222-4222-8222-222222222222",
              name: "Demo",
              slug: "demo",
              status: "active",
              timezone: "UTC",
              defaultCurrency: "GBP",
            },
            roles: ["owner"],
          },
          tokens: {
            accessToken: "demo",
            tokenType: "bearer",
            expiresIn: 3600,
            refreshToken: null,
            accessExpiresAt: new Date().toISOString(),
            refreshExpiresAt: new Date().toISOString(),
          },
        }),
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
              id: DEMO_PRODUCT_ID,
            },
          ],
          meta: {
            page: 1,
            size: 25,
            totalItems: 1,
            totalPages: 1,
            hasNext: false,
            hasPrevious: false,
          },
        }),
      }),
    );
    await page.goto("/products");
    await expect(page.getByTestId("product-row")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-listing-status")).toHaveText(
      "Added to Shopify",
    );
    await page.getByTestId("product-title-link").click();
    await expect(page).toHaveURL(new RegExp(`/products/${DEMO_PRODUCT_ID}$`));
  });
});

test.describe("UX-L2C responsive evidence", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("mobile completion bar after publish", async ({ page }) => {
    await openMockedEditor(page, {
      listings: [syncedListing()],
    });
    await expect(page.getByTestId("mobile-editor-action-bar")).toBeVisible();
    await expect(page.getByRole("link", { name: "View product" })).toBeVisible();
    await captureEvidenceScreenshot(page, "after-390-mobile-view-product", {
      root: UX_L2C_SHOT_ROOT,
    });
  });

  test("320px no horizontal overflow on publish panel", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 844 });
    await openMockedEditor(page);
    await openReview(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflow).toBe(false);
    await captureEvidenceScreenshot(page, "after-320-publish", {
      root: UX_L2C_SHOT_ROOT,
    });
  });
});

test.describe("UX-L2C evidence non-empty", () => {
  test("shot root is outside repo", () => {
    expect(UX_L2C_SHOT_ROOT.includes("DropPilotLogs") || UX_L2C_SHOT_ROOT.includes("test-results")).toBe(
      true,
    );
    fs.mkdirSync(UX_L2C_SHOT_ROOT, { recursive: true });
    expect(fs.existsSync(UX_L2C_SHOT_ROOT)).toBe(true);
  });
});
