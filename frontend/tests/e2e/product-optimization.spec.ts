import { expect, test, type Page } from "@playwright/test";

import { isApiReachable } from "./helpers/auth";
import {
  registerViaApi,
  seedCatalogueViaApi,
  signInWithAccount,
} from "./helpers/catalogue";

/**
 * Phase 9 stage 3 — product optimisation foundation UI.
 *
 * Two layers:
 * 1. Route-mocked specs that always run (button, history empty state, error).
 * 2. Live-seeded specs that exercise StubProvider end-to-end when AliExpress
 *    OAuth+import can complete; otherwise skip — same posture as products.spec.
 */

const PRODUCT_ID = "11111111-1111-1111-1111-111111111111";

function mockProductList(page: Page, aiStatus: "not_optimized" | "optimized" | "failed") {
  return page.route("**/api/v1/products?**", async (route) => {
    if (route.request().method() !== "GET") {
      return route.continue();
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: PRODUCT_ID,
            source: "aliexpress",
            externalId: "3256806389000685",
            title: "Stub catalogue product for optimisation UI",
            status: "draft",
            currency: "USD",
            costPriceMin: "9.99",
            costPriceMax: "9.99",
            stockQuantity: 10,
            supplierName: "Stub Supplier",
            categoryId: null,
            categoryName: "Home",
            brand: null,
            seoTitle: null,
            seoDescription: null,
            metaKeywords: null,
            slug: null,
            vendor: null,
            tags: [],
            aiStatus,
            aiLastGeneratedAt: null,
            aiProvider: aiStatus === "optimized" ? "stub" : null,
            aiVersion: aiStatus === "optimized" ? 2 : null,
            optimizedTitle: aiStatus === "optimized" ? "[STUB-AI] Title" : null,
            optimizedDescription: null,
            rating: null,
            reviewCount: null,
            orderCount: null,
            lastSyncedAt: null,
            lastSyncError: null,
            createdAt: "2026-08-02T00:00:00Z",
            updatedAt: "2026-08-02T00:00:00Z",
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
    });
  });
}

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run product optimisation tests.",
  );
});

test.describe("Product optimization foundation (mocked catalogue)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("shows the optimize button, AI status, and history control", async ({
    page,
    request,
  }) => {
    // API registration + UI sign-in avoids UI-registration flakes under parallel load.
    const { account } = await registerViaApi(request);
    await signInWithAccount(page, account);
    await mockProductList(page, "not_optimized");
    await page.goto("/products");

    // `product-row` is the table row only (the phone card is `product-card`
    // since the UX-L2D-07 remediation), so one product is exactly one row;
    // the badge is read inside that row because the card carries its own.
    const row = page.getByTestId("product-row");
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("ai-status-badge")).toHaveText("Not optimized");
    await expect(
      page.getByRole("button", { name: "Optimize with AI" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "History" })).toBeVisible();
  });

  test("version history sheet shows the empty state before any optimisation", async ({
    page,
    request,
  }) => {
    const { account } = await registerViaApi(request);
    await signInWithAccount(page, account);
    await mockProductList(page, "not_optimized");
    await page.route(`**/api/v1/products/${PRODUCT_ID}/versions**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [],
          meta: {
            page: 1,
            size: 50,
            totalItems: 0,
            totalPages: 0,
            hasNext: false,
            hasPrevious: false,
          },
        }),
      });
    });

    await page.goto("/products");
    await page.getByRole("button", { name: "History" }).click();

    await expect(page.getByRole("heading", { name: "Version history" })).toBeVisible();
    await expect(page.getByText(/Not optimized yet/i)).toBeVisible();
  });

  test("surfaces an error when optimisation fails", async ({ page, request }) => {
    const { account } = await registerViaApi(request);
    await signInWithAccount(page, account);
    await mockProductList(page, "not_optimized");
    await page.route(`**/api/v1/products/${PRODUCT_ID}/optimize`, async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          code: "ai_provider_unavailable",
          message: "AI provider is not configured.",
          details: [],
          requestId: "e2e-opt-fail",
        }),
      });
    });

    await page.goto("/products");
    await page.getByRole("button", { name: "Optimize with AI" }).click();

    // Next.js also mounts a route announcer with role=alert — scope to the row.
    await expect(
      page.getByTestId("product-row").getByText("AI provider is not configured."),
    ).toBeVisible();
  });
});

test.describe("Product optimization foundation (live seed)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("optimises a seeded product and shows version history", async ({
    page,
    request,
  }) => {
    const seeded = await seedCatalogueViaApi(request);
    test.skip(
      seeded === null,
      "Catalogue seeding failed — OAuth or import could not complete against this backend.",
    );

    await signInWithAccount(page, seeded.account);
    await page.goto("/drafts");

    const row = page.getByTestId("draft-row");
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("ai-status-badge")).toHaveText("Not optimized");

    const optimizeResponse = page.waitForResponse(
      (r) => r.url().includes("/optimize") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Optimize with AI" }).click();
    expect((await optimizeResponse).status()).toBe(201);

    await expect(row.getByTestId("ai-status-badge")).toHaveText("Optimized", {
      timeout: 15_000,
    });

    await page.getByRole("button", { name: "History" }).click();
    await expect(page.getByRole("heading", { name: "Version history" })).toBeVisible();
    await expect(page.getByTestId("product-version-row")).toHaveCount(2);
    await expect(page.getByText("Original")).toBeVisible();
    await expect(page.getByText("AI generated")).toBeVisible();
    await expect(page.getByText("Active")).toBeVisible();
  });
});
