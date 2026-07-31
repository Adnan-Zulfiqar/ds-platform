import { expect, test } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * Product catalogue tests.
 *
 * Run against the real API, so what the page shows is genuinely the server's
 * state.
 *
 * **The successful-import path is not covered here, deliberately.** Importing
 * requires a connected AliExpress account, and connecting requires completing
 * an OAuth consent screen on a live third-party site. A browser test cannot do
 * that, and stubbing it would only test the stub. That path is covered by the
 * backend integration suite, which drives the real HTTP pipeline against the
 * captured supplier payload, and it was additionally verified end to end
 * against the live gateway during Phase 4.
 *
 * What *is* covered here is everything the browser genuinely owns: routing,
 * authentication, the empty state, the import dialog's behaviour, and the
 * failure a user actually hits first — importing with no supplier connected.
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

    await expect(page.getByText("Enter an AliExpress product ID.")).toBeVisible();
    expect(requested).toBe(false);
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
