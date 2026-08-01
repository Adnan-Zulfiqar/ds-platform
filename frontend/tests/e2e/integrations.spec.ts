import { expect, test } from "@playwright/test";

import { isApiReachable, isRedisAvailable, registerAndSignIn } from "./helpers/auth";

/**
 * Integration settings tests.
 *
 * Run against the real API. Merchants never enter AliExpress app credentials —
 * connect uses platform ``ALIEXPRESS_APP_*``. Tests exercise states reachable
 * without completing a live OAuth round trip: not connected, pending after
 * starting connect (redirect blocked), and disconnected again.
 */

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run integration tests.",
  );
});

/**
 * Start connect and wait for the server to acknowledge (201).
 *
 * The redirect target is a real external site; it is blocked so the test can
 * observe pending state instead of leaving the app.
 */
async function beginAliExpressConnect(
  page: import("@playwright/test").Page,
): Promise<void> {
  test.skip(
    !(await isRedisAvailable()),
    "Redis is not available — the OAuth state store is required to begin a connection.",
  );

  await page.route("**/oauth/authorize*", (route) => route.abort());

  const suppliers = page.getByRole("region", { name: "Suppliers" });
  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/integrations/aliexpress/connect"),
  );
  await suppliers.getByRole("button", { name: "Connect AliExpress" }).click();

  const response = await responsePromise;
  const status = response.status();
  const detail =
    status === 201
      ? ""
      : await response
          .text()
          .catch(() => "(body unavailable: the page navigated away)");

  expect(status, detail).toBe(201);
}

test.describe("Integrations page", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("is reachable from settings", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings");

    await page.getByRole("link", { name: /Integrations/ }).click();

    await expect(page).toHaveURL(/\/settings\/integrations$/);
    await expect(
      page.getByRole("heading", { name: "Integrations", level: 1 }),
    ).toBeVisible();
  });

  test("shows AliExpress as not connected for a new workspace", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const suppliers = page.getByRole("region", { name: "Suppliers" });

    await expect(suppliers.getByRole("heading", { name: "AliExpress" })).toBeVisible();
    await expect(suppliers.getByText("Not connected")).toBeVisible();
    await expect(
      suppliers.getByRole("button", { name: "Connect AliExpress" }),
    ).toBeVisible();
  });

  test("AliExpress connect has no merchant credential fields", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const suppliers = page.getByRole("region", { name: "Suppliers" });
    await expect(suppliers.getByLabel("App key")).toHaveCount(0);
    await expect(suppliers.getByLabel("App secret")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("Shopify card is present and planned channels stay unavailable", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const channels = page.getByRole("region", { name: "Sales channels" });
    await expect(channels.getByText("Shopify", { exact: true }).first()).toBeVisible();
    await expect(
      channels.getByRole("button", { name: "Connect Shopify" }),
    ).toBeVisible();
    await expect(channels.getByText("Coming soon").first()).toBeVisible();
    await expect(channels.getByText("WooCommerce")).toBeVisible();
  });

  test("starting connect moves the connection to pending", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await beginAliExpressConnect(page);
    await page.goto("/settings/integrations");

    const suppliers = page.getByRole("region", { name: "Suppliers" });
    await expect(suppliers.getByText("Awaiting authorization")).toBeVisible();
  });

  test("disconnecting returns the card to not connected", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await beginAliExpressConnect(page);
    await page.goto("/settings/integrations");

    const suppliers = page.getByRole("region", { name: "Suppliers" });
    await suppliers.getByRole("button", { name: "Disconnect" }).click();

    await expect(suppliers.getByText("Not connected")).toBeVisible();
  });
});

test.describe("Integrations route protection", () => {
  test("redirects an unauthenticated visitor to sign in", async ({ page }) => {
    await page.goto("/settings/integrations");
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("Integrations responsiveness", () => {
  test("renders without horizontal overflow at 320px", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 720 });
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflows).toBe(false);
  });
});
