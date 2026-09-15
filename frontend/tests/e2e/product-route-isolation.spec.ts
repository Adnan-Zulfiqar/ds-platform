import { expect, test } from "./fixtures/provider-isolation";
import { API_URL, buildAccount, isApiReachable } from "./helpers/auth";
import { registerViaApi, signInWithAccount } from "./helpers/catalogue";
import { canSeed, explainSeedUnavailable, seedDrafts } from "./helpers/seed";

/**
 * `/products/[productId]` against a live backend — tenant isolation and the
 * draft/published boundary (UX-L2D-04).
 *
 * Adapted from the reviewed historical UX-L2C spec (UX-L2D-GATE-04). Kept:
 * the own-product, foreign-tenant, missing-id and malformed-id checks, each
 * asserting the API's answer and the page's answer. Changed: a malformed id
 * must read as "Product not found" (the historical spec accepted a generic
 * error), and a seeded draft opened through the Products route must land in
 * the draft editor rather than render as a published product.
 *
 * Skips without a backend or the E2E seed configuration; runs in CI.
 */

test.describe("Product detail route — tenant isolation", () => {
  test.beforeEach(async () => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");
  });

  test("tenant A can view its own published product page", async ({ page, request }) => {
    test.skip(!(await canSeed()), await explainSeedUnavailable());
    const registered = await registerViaApi(request);
    await seedDrafts(registered.tenantId, 1, { published: 1 });

    const listResponse = await request.get(`${API_URL}/api/v1/products?page=1&size=1`, {
      headers: { Authorization: `Bearer ${registered.accessToken}` },
    });
    expect(listResponse.ok()).toBeTruthy();
    const listBody = (await listResponse.json()) as { items: Array<{ id: string }> };
    test.skip(listBody.items.length === 0, "No products available for tenant A.");

    const productId = listBody.items[0]!.id;
    await signInWithAccount(page, registered.account, `/products/${productId}`);
    await expect(page.getByTestId("published-product-summary")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("published-product-lifecycle")).not.toHaveAttribute(
      "data-kind",
      "not-published",
    );
    await expect(page.getByText(/tenant/i)).toHaveCount(0);
  });

  test("a seeded draft opened through the Products route lands in the draft editor", async ({
    page,
    request,
  }) => {
    test.skip(!(await canSeed()), await explainSeedUnavailable());
    const registered = await registerViaApi(request);
    await seedDrafts(registered.tenantId, 1, { published: 0 });

    const listResponse = await request.get(`${API_URL}/api/v1/drafts?page=1&size=1`, {
      headers: { Authorization: `Bearer ${registered.accessToken}` },
    });
    expect(listResponse.ok()).toBeTruthy();
    const listBody = (await listResponse.json()) as { items: Array<{ id: string }> };
    test.skip(listBody.items.length === 0, "No drafts available for tenant A.");

    const draftId = listBody.items[0]!.id;
    await signInWithAccount(page, registered.account, "/dashboard");
    await page.goto(`/products/${draftId}`);
    await expect(page).toHaveURL(new RegExp(`/drafts/${draftId}$`), { timeout: 30_000 });
    await expect(page.getByTestId("published-product-summary")).toHaveCount(0);
  });

  test("tenant B requesting tenant A's product gets a non-disclosing not found", async ({
    browser,
    request,
  }) => {
    test.skip(!(await canSeed()), await explainSeedUnavailable());
    const registeredA = await registerViaApi(request);
    await seedDrafts(registeredA.tenantId, 1, { published: 1 });

    const listResponse = await request.get(`${API_URL}/api/v1/products?page=1&size=1`, {
      headers: { Authorization: `Bearer ${registeredA.accessToken}` },
    });
    test.skip(!listResponse.ok(), "Tenant A has no product list.");
    const listBody = (await listResponse.json()) as { items: Array<{ id: string }> };
    test.skip(listBody.items.length === 0, "No products for tenant A.");
    const foreignId = listBody.items[0]!.id;

    const registeredB = await registerViaApi(request, buildAccount());
    const contextB = await browser.newContext();
    const pageB = await contextB.newPage();
    try {
      // The API answers 404, not 403 — nothing confirms the row exists.
      const apiResponse = await request.get(`${API_URL}/api/v1/products/${foreignId}`, {
        headers: { Authorization: `Bearer ${registeredB.accessToken}` },
      });
      expect(apiResponse.status()).toBe(404);

      await signInWithAccount(pageB, registeredB.account, "/dashboard");
      await pageB.goto(`/products/${foreignId}`);
      await expect(pageB.getByTestId("published-product-not-found")).toBeVisible({ timeout: 30_000 });
      await expect(pageB.getByText(/tenant|workspace/i)).toHaveCount(0);
    } finally {
      await contextB.close();
    }
  });

  test("a missing product id reads exactly like a foreign one", async ({ page, request }) => {
    const registered = await registerViaApi(request);
    const missingId = "00000000-0000-4000-8000-000000000099";
    const apiResponse = await request.get(`${API_URL}/api/v1/products/${missingId}`, {
      headers: { Authorization: `Bearer ${registered.accessToken}` },
    });
    expect(apiResponse.status()).toBe(404);

    await signInWithAccount(page, registered.account, "/dashboard");
    await page.goto(`/products/${missingId}`);
    await expect(page.getByTestId("published-product-not-found")).toBeVisible({ timeout: 30_000 });
  });

  test("a malformed id is answered by the route, not by a 422 from the API", async ({
    page,
    request,
  }) => {
    const registered = await registerViaApi(request);
    const apiResponse = await request.get(`${API_URL}/api/v1/products/not-a-uuid`, {
      headers: { Authorization: `Bearer ${registered.accessToken}` },
    });
    expect([404, 422]).toContain(apiResponse.status());

    const detailRequests: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/v1/products/not-a-uuid")) detailRequests.push(r.url());
    });
    await signInWithAccount(page, registered.account, "/dashboard");
    await page.goto("/products/not-a-uuid");
    await expect(page.getByTestId("published-product-not-found")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("published-product-error")).toHaveCount(0);
    expect(detailRequests).toHaveLength(0);
  });
});
