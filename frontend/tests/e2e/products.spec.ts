import { expect, test } from "@playwright/test";

import { isApiReachable, isRedisAvailable, registerAndSignIn, API_URL } from "./helpers/auth";
import {
  FIXTURE_PRODUCT_ID,
  connectAliExpressViaApi,
  registerViaApi,
  seedCatalogueViaApi,
  signInWithAccount,
} from "./helpers/catalogue";

/**
 * Product catalogue tests.
 *
 * Run against the real API, so what the page shows is genuinely the server's
 * state. The import-flow suite seeds connection and catalogue rows through the
 * same HTTP endpoints the UI uses; when the live AliExpress gateway rejects the
 * synthetic OAuth code those tests skip rather than fail. Parsing and storage
 * are covered by the backend integration suite against captured payloads.
 */

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run product tests.",
  );
});

test.describe("Products page", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("is reachable from the sidebar", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/dashboard");

    await page
      .getByRole("navigation")
      .first()
      .getByRole("link", { name: "Products" })
      .click();

    await expect(page).toHaveURL(/\/products$/);
    await expect(
      page.getByRole("heading", { name: "Products", level: 1 }),
    ).toBeVisible();
  });

  test("shows an empty state before anything is imported", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/products");

    // An empty state, not an error state. Nothing has gone wrong — a new
    // workspace legitimately has no products, and it should be told the next
    // useful step rather than shown a failure.
    await expect(page.getByText("No products yet")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Import product" }).first(),
    ).toBeVisible();
  });

  test("the import dialog opens and explains what it needs", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/products");

    await page.getByRole("button", { name: "Import product" }).first().click();

    await expect(
      page.getByRole("heading", { name: "Import from AliExpress" }),
    ).toBeVisible();
    await expect(page.getByLabel("AliExpress product ID")).toBeVisible();
  });

  test("rejects an empty product id without calling the server", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/products");

    let requested = false;
    await page.route("**/products/import", (route) => {
      requested = true;
      return route.abort();
    });

    await page.getByRole("button", { name: "Import product" }).first().click();
    await page.getByRole("button", { name: "Import", exact: true }).click();

    await expect(
      page.getByText(/Enter an AliExpress product ID/),
    ).toBeVisible();
    expect(requested).toBe(false);
  });

  test("accepts a pasted listing URL, not just a bare ID", async ({ page }) => {
    /**
     * The regression this guards. A full URL used to be forwarded verbatim, and
     * AliExpress answers a malformed ID with "the input parameter product_id is
     * not supplied" — reporting it as missing rather than wrong, which sends
     * you hunting a serialisation bug that is not there.
     *
     * Asserting on the request body rather than the outcome: the import itself
     * needs a connected supplier, which this test does not have.
     */
    await registerAndSignIn(page);
    await page.goto("/products");

    let sentId: string | null = null;
    await page.route("**/products/import", async (route) => {
      const body = route.request().postDataJSON() as { externalId?: string };
      sentId = body.externalId ?? null;
      return route.abort();
    });

    await page.getByRole("button", { name: "Import product" }).first().click();
    await page
      .getByLabel(/AliExpress product ID/)
      .fill("https://www.aliexpress.com/item/1005009558589813.html");
    await page.getByRole("button", { name: "Import", exact: true }).click();

    await expect(() => expect(sentId).toBe("1005009558589813")).toPass();
  });

  test("surfaces the server's reason when no supplier is connected", async ({
    page,
  }) => {
    /**
     * The first failure a real user meets: they find a product ID, paste it,
     * and have not connected AliExpress yet. The dialog must say so rather than
     * failing silently or showing a generic message.
     */
    await registerAndSignIn(page);
    await page.goto("/products");

    await page.getByRole("button", { name: "Import product" }).first().click();
    await page.getByLabel("AliExpress product ID").fill("3256806389000685");

    const response = page.waitForResponse((r) =>
      r.url().includes("/products/import"),
    );
    await page.getByRole("button", { name: "Import", exact: true }).click();

    expect((await response).status()).toBe(409);
    await expect(page.getByRole("alert")).toBeVisible();
  });

  test("the dialog can be dismissed", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/products");

    await page.getByRole("button", { name: "Import product" }).first().click();
    await page.getByRole("button", { name: "Cancel" }).click();

    await expect(
      page.getByRole("heading", { name: "Import from AliExpress" }),
    ).toBeHidden();
  });
});

test.describe("Products import flow", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test.beforeAll(async () => {
    test.skip(
      !(await isApiReachable()),
      "Backend API is not reachable — start it to run product import flow tests.",
    );
    test.skip(
      !(await isRedisAvailable()),
      "Redis is not available — OAuth state storage is required to connect AliExpress.",
    );
  });

  test("shows AliExpress as connected after OAuth completes", async ({ page, request }) => {
    const { account, accessToken } = await registerViaApi(request);
    const connected = await connectAliExpressViaApi(request, accessToken);
    test.skip(
      !connected,
      "AliExpress OAuth callback did not complete — live gateway rejects the synthetic auth code.",
    );

    const status = await request.get(
      `${API_URL}/api/v1/integrations/aliexpress/status`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    expect((await status.json()).connected).toBe(true);

    await signInWithAccount(page, account);
    await page.goto("/settings/integrations");

    const suppliers = page.getByRole("region", { name: "Suppliers" });
    await expect(suppliers.getByText("Not connected")).toBeHidden();
    await expect(suppliers.getByText("Connection error")).toBeHidden();
  });

  test("displays an imported product after API seeding", async ({ page, request }) => {
    const seeded = await seedCatalogueViaApi(request);
    test.skip(
      seeded === null,
      "Catalogue seeding failed — OAuth or import could not complete against this backend.",
    );

    await signInWithAccount(page, seeded.account);
    await page.goto("/products");

    await expect(page.getByTestId("product-row")).toHaveCount(1);
    await expect(page.getByText(seeded.product.title.slice(0, 20))).toBeVisible();
    await expect(page.getByText(seeded.product.externalId)).toBeVisible();
  });

  test("imports through the dialog when AliExpress is connected", async ({
    page,
    request,
  }) => {
    const { account, accessToken } = await registerViaApi(request);
    const connected = await connectAliExpressViaApi(request, accessToken);
    test.skip(
      !connected,
      "AliExpress OAuth callback did not complete — live gateway rejects the synthetic auth code.",
    );

    await signInWithAccount(page, account);
    await page.goto("/products");

    await page.getByRole("button", { name: "Import product" }).first().click();
    await page.getByLabel("AliExpress product ID").fill(FIXTURE_PRODUCT_ID);

    const response = page.waitForResponse((r) =>
      r.url().includes("/products/import"),
    );
    await page.getByRole("button", { name: "Import", exact: true }).click();

    expect((await response).status()).toBe(201);
    await expect(page.getByTestId("product-row")).toHaveCount(1);
    await expect(page.getByText(FIXTURE_PRODUCT_ID)).toBeVisible();
  });
});

test.describe("Products route protection", () => {
  test("redirects an unauthenticated visitor to sign in", async ({ page }) => {
    await page.goto("/products");
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("Products responsiveness", () => {
  test.use({ viewport: { width: 320, height: 720 } });

  test("renders without horizontal overflow at 320px", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/products");

    await expect(
      page.getByRole("heading", { name: "Products", level: 1 }),
    ).toBeVisible();

    // The table scrolls inside its own container; the page body must not.
    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(overflows).toBe(false);
  });
});
