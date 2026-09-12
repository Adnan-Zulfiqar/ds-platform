import { expect, test } from "@playwright/test";

import {
  API_URL,
  blockGoogleIdentityScript,
  buildAccount,
  isApiReachable,
} from "./helpers/auth";
import { registerViaApi, signInWithAccount } from "./helpers/catalogue";
import { canSeed, explainSeedUnavailable, seedDrafts } from "./helpers/seed";

test.describe("Product detail route — tenant isolation", () => {
  test.beforeEach(async () => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");
  });

  test("tenant A can view its own product page", async ({ page, request }) => {
    test.skip(!(await canSeed()), await explainSeedUnavailable());
    await blockGoogleIdentityScript(page);
    const registered = await registerViaApi(request);
    await seedDrafts(registered.tenantId, 1, { published: 1 });

    const listResponse = await request.get(`${API_URL}/api/v1/products?page=1&size=1`, {
      headers: { Authorization: `Bearer ${registered.accessToken}` },
    });
    expect(listResponse.ok()).toBeTruthy();
    const listBody = (await listResponse.json()) as {
      items: Array<{ id: string }>;
    };
    test.skip(listBody.items.length === 0, "No products available for tenant A.");

    const productId = listBody.items[0]!.id;
    await signInWithAccount(page, registered.account, `/products/${productId}`);
    await expect(page.getByTestId("published-product-summary")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText(/tenant/i)).toHaveCount(0);
  });

  test("tenant A requesting tenant B product receives 404", async ({
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
    const listBody = (await listResponse.json()) as {
      items: Array<{ id: string }>;
    };
    test.skip(listBody.items.length === 0, "No products for tenant A.");
    const foreignId = listBody.items[0]!.id;

    const registeredB = await registerViaApi(request, buildAccount());
    const contextB = await browser.newContext();
    const pageB = await contextB.newPage();
    await blockGoogleIdentityScript(pageB);
    await signInWithAccount(pageB, registeredB.account, "/dashboard");

    const apiResponse = await request.get(`${API_URL}/api/v1/products/${foreignId}`, {
      headers: { Authorization: `Bearer ${registeredB.accessToken}` },
    });
    expect(apiResponse.status()).toBe(404);

    await pageB.goto(`/products/${foreignId}`);
    await expect(pageB.getByTestId("published-product-not-found")).toBeVisible({
      timeout: 30_000,
    });

    await contextB.close();
  });

  test("random missing product id uses non-disclosing 404", async ({
    page,
    request,
  }) => {
    await blockGoogleIdentityScript(page);
    const registered = await registerViaApi(request);
    await signInWithAccount(page, registered.account, "/dashboard");

    const missingId = "00000000-0000-4000-8000-000000000099";
    const apiResponse = await request.get(`${API_URL}/api/v1/products/${missingId}`, {
      headers: { Authorization: `Bearer ${registered.accessToken}` },
    });
    expect(apiResponse.status()).toBe(404);

    await page.goto(`/products/${missingId}`);
    await expect(page.getByTestId("published-product-not-found")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("invalid uuid uses safe error response", async ({ page, request }) => {
    await blockGoogleIdentityScript(page);
    const registered = await registerViaApi(request);
    await signInWithAccount(page, registered.account, "/dashboard");

    const apiResponse = await request.get(`${API_URL}/api/v1/products/not-a-uuid`, {
      headers: { Authorization: `Bearer ${registered.accessToken}` },
    });
    expect([404, 422]).toContain(apiResponse.status());

    await page.goto("/products/not-a-uuid");
    await expect(
      page
        .getByTestId("published-product-not-found")
        .or(page.getByTestId("published-product-error")),
    ).toBeVisible({ timeout: 30_000 });
  });
});
