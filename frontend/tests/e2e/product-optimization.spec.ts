import { expect, test, type Page } from "@playwright/test";

import { API_URL, isApiReachable } from "./helpers/auth";
import { registerViaApi, signInWithAccount } from "./helpers/catalogue";
import { canSeed, seedDrafts } from "./helpers/seed";

/**
 * Product optimisation in the catalogue, after Phase 9 Stage 10.
 *
 * The Stage 3 "Optimize with AI" button generated *and activated* AI text in
 * one click. Stage 10 retired it from every primary screen in favour of AI
 * Studio, where nothing is approved or published without a confirmation.
 *
 * Two layers:
 * 1. Route-mocked catalogue: the row links to AI Studio and nothing calls the
 *    legacy endpoint (which the backend keeps).
 * 2. Live: a draft seeded straight into the test database (no AliExpress
 *    OAuth) goes through the real API with StubProvider — preview, approve,
 *    and the server refusing to publish synthetic text.
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

test.describe("Catalogue entry point (mocked catalogue)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("rows link to AI Studio, keep the AI status and history control, and never optimize in place", async ({
    page,
    request,
  }) => {
    let optimizeCalls = 0;
    // API registration + UI sign-in avoids UI-registration flakes under parallel load.
    const { account } = await registerViaApi(request);
    await signInWithAccount(page, account);
    await mockProductList(page, "not_optimized");
    await page.route("**/api/v1/products/*/optimize", (route) => {
      optimizeCalls += 1;
      return route.fulfill({ status: 500, body: "{}" });
    });
    await page.goto("/products");

    // `product-row` is the table row only (the phone card is `product-card`
    // since the UX-L2D-07 remediation), so one product is exactly one row;
    // the badge is read inside that row because the card carries its own.
    const row = page.getByTestId("product-row");
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("ai-status-badge")).toHaveText("Not optimized");
    await expect(page.getByRole("button", { name: "Optimize with AI" })).toHaveCount(0);
    await expect(row.getByRole("link", { name: "AI Studio" })).toHaveAttribute(
      "href",
      `/ai-studio/products/${PRODUCT_ID}`,
    );
    await expect(page.getByRole("button", { name: "History" })).toBeVisible();
    expect(optimizeCalls).toBe(0);
  });

  test("version history points an empty product at AI Studio", async ({ page, request }) => {
    const { account } = await registerViaApi(request);
    await signInWithAccount(page, account);
    await mockProductList(page, "not_optimized");
    await page.route(`**/api/v1/products/${PRODUCT_ID}/versions**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [],
          meta: { page: 1, size: 50, totalItems: 0, totalPages: 0, hasNext: false, hasPrevious: false },
        }),
      });
    });

    await page.goto("/products");
    await page.getByRole("button", { name: "History" }).click();

    await expect(page.getByRole("heading", { name: "Version history" })).toBeVisible();
    await expect(page.getByText(/No AI versions yet\. Use AI Studio/)).toBeVisible();
    await expect(page.getByText(/Optimize with AI/)).toHaveCount(0);
  });
});

test.describe("AI Studio with StubProvider (live backend, seeded draft)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("preview, approve, and the server refusing to publish synthetic text", async ({ page, request }) => {
    test.skip(!(await canSeed()), "Draft seeding is not available in this environment.");
    const { account, accessToken, tenantId } = await registerViaApi(request);
    await seedDrafts(tenantId, 1, { prefix: "Studio lamp" });
    const auth = { Authorization: `Bearer ${accessToken}` };
    const drafts = await request.get(`${API_URL}/api/v1/drafts`, { headers: auth });
    expect(drafts.ok(), await drafts.text()).toBeTruthy();
    const productId = ((await drafts.json()) as { items: { id: string }[] }).items[0]?.id;
    expect(productId).toBeTruthy();

    await signInWithAccount(page, account);
    await page.goto(`/ai-studio/products/${productId}`);

    const generated = page.waitForResponse(
      (r) => r.url().endsWith(`/products/${productId}/pipeline/preview`) && r.request().method() === "POST",
    );
    await page.getByTestId("ai-studio-generate").click();
    expect((await generated).status()).toBe(201);
    await expect(page).toHaveURL(/\?candidate=/);
    await expect(page.getByTestId("ai-studio-test-preview")).toContainText("Test AI preview");
    const candidateId = new URL(page.url()).searchParams.get("candidate");
    expect(candidateId).toBeTruthy();

    const approved = page.waitForResponse(
      (r) => r.url().endsWith(`/pipeline/versions/${candidateId}/approve`) && r.request().method() === "POST",
    );
    await page.getByTestId("ai-studio-approve").click();
    await page.getByTestId("ai-studio-approve-confirm").click();
    expect((await approved).status()).toBe(200);
    await expect(page.getByTestId("ai-studio-approved")).toBeVisible();
    await expect(page.getByTestId("ai-studio-approved-badge")).toBeVisible();
    // Synthetic text never gets a publish button that works.
    await expect(page.getByTestId("ai-studio-publish")).toBeDisabled();

    // And the server refuses it even when asked directly.
    const product = await request.get(`${API_URL}/api/v1/products/${productId}`, { headers: auth });
    const updatedAt = ((await product.json()) as { updatedAt: string }).updatedAt;
    const forced = await request.post(
      `${API_URL}/api/v1/products/${productId}/pipeline/versions/${candidateId}/publish`,
      {
        headers: auth,
        data: { storeId: "00000000-0000-4000-8000-000000000000", expectedUpdatedAt: updatedAt },
      },
    );
    expect(forced.status()).toBe(422);
    const body = (await forced.json()) as { details: { type: string; message: string }[] };
    expect(body.details).toContainEqual(
      expect.objectContaining({ type: "reason", message: "synthetic_publish_blocked" }),
    );
  });
});
