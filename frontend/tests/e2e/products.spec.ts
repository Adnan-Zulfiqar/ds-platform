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
 * Product workspace tests (Drafts vs Products — Product Workspace V2 Stage 0).
 *
 * Imports land in Drafts. Products stays empty until a channel listing is
 * synced. Run against the real API when available.
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
      .getByRole("link", { name: /^Products/ })
      .click();

    await expect(page).toHaveURL(/\/products$/);
    await expect(
      page.getByRole("heading", { name: "Products", level: 1 }),
    ).toBeVisible();
  });

  test("shows an empty published state before anything is published", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/products");

    await expect(page.getByText("No published products yet")).toBeVisible();
    await expect(page.getByRole("link", { name: "Go to Drafts" })).toBeVisible();
  });
});

test.describe("Drafts page", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("is reachable from the sidebar", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/dashboard");

    await page
      .getByRole("navigation")
      .first()
      .getByRole("link", { name: /^Drafts/ })
      .click();

    await expect(page).toHaveURL(/\/drafts$/);
    await expect(
      page.getByRole("heading", { name: "Drafts", level: 1 }),
    ).toBeVisible();
  });

  test("shows an empty state before anything is imported", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    await expect(page.getByText("No drafts yet")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Import as Draft" }).first(),
    ).toBeVisible();
  });

  test("the import dialog opens and explains what it needs", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    await page.getByRole("button", { name: "Import as Draft" }).first().click();

    await expect(
      page.getByRole("heading", { name: "Import as Draft from AliExpress" }),
    ).toBeVisible();
    await expect(page.getByLabel(/AliExpress product ID/)).toBeVisible();
  });

  test("rejects an empty product id without calling the server", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    let requested = false;
    await page.route("**/products/import", (route) => {
      requested = true;
      return route.abort();
    });

    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page.getByTestId("import-ship-to").selectOption("US");
    await page.getByTestId("import-as-draft-submit").click();

    await expect(
      page.getByText(/Enter an AliExpress product ID/),
    ).toBeVisible();
    expect(requested).toBe(false);
  });

  test("accepts a pasted listing URL, not just a bare ID", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    let sentId: string | null = null;
    let sentShipTo: string | null = null;
    await page.route("**/products/import", async (route) => {
      const body = route.request().postDataJSON() as {
        externalId?: string;
        shipToCountry?: string;
      };
      sentId = body.externalId ?? null;
      sentShipTo = body.shipToCountry ?? null;
      return route.abort();
    });

    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page
      .getByTestId("import-external-id")
      .fill("https://www.aliexpress.com/item/1005009558589813.html");
    await page.getByTestId("import-ship-to").selectOption("GB");
    await page.getByTestId("import-as-draft-submit").click();

    await expect(() => expect(sentId).toBe("1005009558589813")).toPass();
    await expect(() => expect(sentShipTo).toBe("GB")).toPass();
  });

  test("keeps the URL when destination is prohibited and country changes", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    await page.route("**/products/import", async (route) => {
      const body = route.request().postDataJSON() as { shipToCountry?: string };
      if (body.shipToCountry === "US") {
        return route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            code: "aliexpress_ship_to_prohibited",
            message:
              "This product cannot currently be shipped to United States through your connected AliExpress account.",
            details: [],
            requestId: "test-req-482",
          }),
        });
      }
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "11111111-1111-1111-1111-111111111111",
          source: "aliexpress",
          externalId: "1005010486653604",
          title: "Anti-Snoring Mouthpiece",
          status: "draft",
          stockQuantity: 0,
          tags: [],
          aiStatus: "not_optimized",
          variants: [],
          images: [],
          importShipToCountry: "GB",
          createdAt: new Date().toISOString(),
        }),
      });
    });

    const url =
      "https://www.aliexpress.com/item/1005010486653604.html";
    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page.getByTestId("import-external-id").fill(url);
    await page.getByTestId("import-ship-to").selectOption("US");
    await page.getByTestId("import-as-draft-submit").click();

    await expect(page.getByTestId("import-feedback")).toContainText(
      /Destination not available|United States/i,
    );
    await expect(page.getByTestId("import-external-id")).toHaveValue(url);

    await page.getByTestId("import-ship-to").selectOption("GB");
    await page.getByTestId("import-as-draft-submit").click();

    await expect(page.getByTestId("import-feedback")).toContainText(/Draft ready/i);
    await expect(page.getByRole("link", { name: "View Draft" })).toBeVisible();
  });

  test("surfaces the server's reason when no supplier is connected", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page.getByTestId("import-external-id").fill("3256806389000685");
    await page.getByTestId("import-ship-to").selectOption("US");

    // Match the POST import only — GET `/products/import/check` also contains
    // that path segment and returns 200 while the submit is still validating.
    const response = page.waitForResponse((r) => {
      if (r.request().method() !== "POST") return false;
      try {
        return /\/api\/v1\/products\/import\/?$/.test(new URL(r.url()).pathname);
      } catch {
        return false;
      }
    });
    await page.getByTestId("import-as-draft-submit").click();

    expect((await response).status()).toBe(409);
    await expect(page.getByRole("alert")).toBeVisible();
  });

  test("the dialog can be dismissed", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page.getByRole("button", { name: "Cancel" }).click();

    await expect(
      page.getByRole("heading", { name: "Import as Draft from AliExpress" }),
    ).toBeHidden();
  });

  test("shows an accurate variant count per draft row", async ({ page }) => {
    await registerAndSignIn(page);

    // Scoped to the API path specifically — a bare "**/drafts*" also matches
    // the page's own document navigation to http://.../drafts, which would
    // replace the whole page with this mocked JSON body instead of just
    // answering the fetch call.
    await page.route("**/api/v1/drafts*", (route) => {
      if (route.request().method() !== "GET") return route.continue();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "66666666-6666-6666-6666-666666666666",
              source: "aliexpress",
              externalId: "1005010486653604",
              title: "Multi-variant Widget",
              status: "draft",
              stockQuantity: 10,
              variantCount: 12,
              tags: [],
              aiStatus: "not_optimized",
              createdAt: new Date().toISOString(),
            },
          ],
          meta: { page: 1, size: 25, totalItems: 1, totalPages: 1, hasNext: false, hasPrevious: false },
        }),
      });
    });

    await page.goto("/drafts");

    await expect(page.getByRole("columnheader", { name: "Variants" })).toBeVisible();
    await expect(page.getByTestId("variant-count")).toHaveText("12");
  });

  test("warns when the entered id matches an already-imported draft (server-authoritative)", async ({
    page,
  }) => {
    // The check is a direct server lookup (GET /products/import/check), not
    // a scan of whatever Drafts page happens to be cached — so the mock
    // below deliberately returns an EMPTY Drafts list. If the warning were
    // still driven by a client-side cache scan, it could never fire here;
    // proving it does proves the lookup is authoritative, not page-bound.
    await registerAndSignIn(page);

    const existingId = "44444444-4444-4444-4444-444444444444";
    const matchingExternalId = "1005010486653604";
    // Scoped to the API path specifically — a bare "**/drafts*" also matches
    // the page's own document navigation to http://.../drafts, which would
    // replace the whole page with this mocked JSON body instead of just
    // answering the fetch call.
    await page.route("**/api/v1/drafts*", (route) => {
      if (route.request().method() !== "GET") return route.continue();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [],
          meta: { page: 1, size: 25, totalItems: 0, totalPages: 0, hasNext: false, hasPrevious: false },
        }),
      });
    });
    await page.route("**/products/import/check*", (route) => {
      const url = new URL(route.request().url());
      const externalId = url.searchParams.get("external_id");
      if (externalId !== matchingExternalId) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ exists: false, product: null }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          exists: true,
          product: {
            id: existingId,
            title: "Anti-Snoring Mouthpiece",
            status: "draft",
            isPublished: false,
          },
        }),
      });
    });

    await page.goto("/drafts");
    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page
      .getByTestId("import-external-id")
      .fill(`https://www.aliexpress.com/item/${matchingExternalId}.html`);

    const warning = page.getByTestId("import-duplicate-warning");
    await expect(warning).toBeVisible();
    await expect(warning).toContainText("Already in Drafts");
    await expect(
      warning.getByRole("link", { name: "Anti-Snoring Mouthpiece" }),
    ).toHaveAttribute("href", `/drafts/${existingId}`);

    // A different id must not be flagged.
    await page.getByTestId("import-external-id").fill("9999999999999");
    await expect(warning).toBeHidden();
  });

  test("links a published duplicate to Products, not Drafts", async ({ page }) => {
    await registerAndSignIn(page);

    const existingId = "55555555-5555-5555-5555-555555555555";
    await page.route("**/products/import/check*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          exists: true,
          product: {
            id: existingId,
            title: "Already Published Widget",
            status: "active",
            isPublished: true,
          },
        }),
      }),
    );

    await page.goto("/drafts");
    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page.getByTestId("import-external-id").fill("1005010486653604");

    const warning = page.getByTestId("import-duplicate-warning");
    await expect(warning).toBeVisible();
    await expect(warning).toContainText("Already published");
    await expect(
      warning.getByRole("link", { name: "Already Published Widget" }),
    ).toHaveAttribute("href", `/products/${existingId}`);
  });

  test("the import dialog is usable from the keyboard alone", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/drafts");

    const trigger = page.getByRole("button", { name: "Import as Draft" }).first();
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("heading", { name: "Import as Draft from AliExpress" }),
    ).toBeVisible();

    // Focus must move into the dialog (Radix's focus trap), not stay stranded
    // behind it — a screen-reader user tabbing past the trigger would
    // otherwise land back in the page body with no indication a dialog opened.
    const focusIsInsideDialog = await page.evaluate(
      () => document.activeElement?.closest('[role="dialog"]') !== null,
    );
    expect(focusIsInsideDialog).toBe(true);

    // The field is reachable and labelled — `getByLabel` only resolves via a
    // real <label htmlFor>/aria association, which is what a screen reader
    // announces on focus.
    await page.getByLabel(/AliExpress product ID/).click();
    await page.keyboard.type("1005009558589813");
    await expect(page.getByTestId("import-external-id")).toHaveValue(
      "1005009558589813",
    );

    // Escape is the standard dialog-dismiss key; it must not be swallowed,
    // and focus must return to the trigger rather than vanishing.
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("heading", { name: "Import as Draft from AliExpress" }),
    ).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test("produces no console errors while opening and using the import dialog", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));

    await registerAndSignIn(page);
    await page.goto("/drafts");

    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page.getByTestId("import-external-id").fill("1005009558589813");
    await page.getByTestId("import-ship-to").selectOption("US");
    await page.getByRole("button", { name: "Cancel" }).click();

    // The one expected entry: every page load speculatively calls
    // /auth/refresh to check for an existing session, and a genuinely
    // unauthenticated visit correctly answers 401 — the browser logs any
    // non-2xx resource load as a console error regardless of how cleanly the
    // app itself handles the rejected promise. Real bugs still fail this
    // test; this filter is scoped to that one specific, expected response.
    const unexpected = errors.filter(
      (message) => !/401 \(Unauthorized\)/.test(message),
    );
    expect(unexpected).toEqual([]);
  });
});

test.describe("Drafts import flow", () => {
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

  test("displays an imported draft after API seeding", async ({ page, request }) => {
    const seeded = await seedCatalogueViaApi(request);
    test.skip(
      seeded === null,
      "Catalogue seeding failed — OAuth or import could not complete against this backend.",
    );

    await signInWithAccount(page, seeded.account);
    await page.goto("/drafts");

    await expect(page.getByTestId("draft-row")).toHaveCount(1);
    await expect(page.getByText(seeded.product.title.slice(0, 20))).toBeVisible();
    await expect(page.getByText(seeded.product.externalId)).toBeVisible();

    await page.goto("/products");
    await expect(page.getByText("No published products yet")).toBeVisible();
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
    await page.goto("/drafts");

    await page.getByRole("button", { name: "Import as Draft" }).first().click();
    await page.getByLabel(/AliExpress product ID/).fill(FIXTURE_PRODUCT_ID);

    const response = page.waitForResponse((r) =>
      r.url().includes("/products/import"),
    );
    await page.getByRole("button", { name: "Import as Draft", exact: true }).last().click();

    expect((await response).status()).toBe(201);
    await expect(page.getByTestId("draft-row")).toHaveCount(1);
    await expect(page.getByText(FIXTURE_PRODUCT_ID)).toBeVisible();
  });
});

test.describe("Products route protection", () => {
  test("redirects an unauthenticated visitor to sign in", async ({ page }) => {
    await page.goto("/products");
    await expect(page).toHaveURL(/\/login/);
  });

  test("redirects unauthenticated drafts visitors to sign in", async ({ page }) => {
    await page.goto("/drafts");
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

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(overflows).toBe(false);
  });
});
