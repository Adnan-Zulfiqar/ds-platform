import { expect, test } from "@playwright/test";

import { isApiReachable, isRedisAvailable, registerAndSignIn } from "./helpers/auth";

/**
 * Integration settings tests.
 *
 * Run against the real API, so the connection state shown is genuinely the
 * server's. **No real AliExpress credentials are used**, and none exist — the
 * tests exercise the states reachable without completing an OAuth round trip
 * with a live provider: not connected, pending after submitting credentials,
 * and disconnected again.
 *
 * The OAuth exchange itself is covered by the backend integration suite, which
 * can mock the provider's HTTP responses. A browser test cannot, so pretending
 * otherwise here would only test a stub.
 */

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run integration tests.",
  );
});

/**
 * Submit credentials and wait for the server to acknowledge.
 *
 * Waiting on the response rather than on a redirect matters: on success the app
 * calls `window.location.assign` to leave for AliExpress, and a test that
 * navigates away at the same moment races that. Asserting the status here also
 * means a server-side failure is reported as itself rather than as a confusing
 * "element not found" further down.
 */
async function connectWithCredentials(
  page: import("@playwright/test").Page,
  { appKey = "e2e-app-key", appSecret = "e2e-app-secret-not-real" } = {},
): Promise<void> {
  // The server stores the OAuth `state` in Redis and refuses to start the flow
  // without it, because that token is the CSRF defence for the redirect.
  test.skip(
    !(await isRedisAvailable()),
    "Redis is not available — the OAuth state store is required to begin a connection.",
  );

  // The redirect target is a real external site; block it.
  await page.route("**/oauth/authorize*", (route) => route.abort());

  await page.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("App key").fill(appKey);
  await page.getByLabel("App secret").fill(appSecret);

  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/integrations/aliexpress/connect"),
  );
  await page.getByRole("button", { name: "Continue to AliExpress" }).click();

  const response = await responsePromise;
  expect(response.status(), await response.text()).toBe(201);
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
    /**
     * The state must come from the server, not be assumed. A page that claimed
     * a supplier was connected when it was not would let an operator believe
     * orders are being fulfilled while nothing happens.
     */
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    // Scoped to the suppliers region: "AliExpress" also appears in the card
    // description and in the sales-channel notice, so a bare text query is
    // ambiguous.
    const suppliers = page.getByRole("region", { name: "Suppliers" });

    await expect(suppliers.getByRole("heading", { name: "AliExpress" })).toBeVisible();
    await expect(suppliers.getByText("Not connected")).toBeVisible();
    await expect(suppliers.getByRole("button", { name: "Connect" })).toBeVisible();
  });

  test("unavailable channels offer no connect control", async ({ page }) => {
    // A button that cannot work is worse than no button.
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const channels = page.getByRole("region", { name: "Sales channels" });
    await expect(channels.getByRole("heading", { name: "Shopify" })).toBeVisible();
    await expect(channels.getByText("Coming soon").first()).toBeVisible();
    // No connect control anywhere in the unavailable section.
    await expect(channels.getByRole("button", { name: "Connect" })).toHaveCount(0);
  });

  test("the connect dialog collects both credentials", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await page.getByRole("button", { name: "Connect" }).click();

    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByLabel("App key")).toBeVisible();
    await expect(page.getByLabel("App secret")).toBeVisible();
  });

  test("the app secret field is masked", async ({ page }) => {
    // It is a credential; it must not be readable over someone's shoulder.
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");
    await page.getByRole("button", { name: "Connect" }).click();

    await expect(page.getByLabel("App secret")).toHaveAttribute("type", "password");
  });

  test("both fields are required", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");
    await page.getByRole("button", { name: "Connect" }).click();

    await page.getByRole("button", { name: "Continue to AliExpress" }).click();

    await expect(page.getByText(/both the app key and app secret are required/i)).toBeVisible();
  });

  test("submitting credentials moves the connection to pending", async ({ page }) => {
    /**
     * Stops before leaving for AliExpress: the redirect goes to a real external
     * site. Navigation is blocked so the test observes the state the server
     * recorded rather than following the browser away.
     */
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await connectWithCredentials(page);
    await page.goto("/settings/integrations");

    const suppliers = page.getByRole("region", { name: "Suppliers" });
    await expect(suppliers.getByText("Awaiting authorization")).toBeVisible();
    await expect(suppliers.getByText("e2e-app-key")).toBeVisible();
  });

  test("the app secret never appears on the page after submission", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await connectWithCredentials(page);
    await page.goto("/settings/integrations");

    // The API has no field capable of returning it, so it cannot come back.
    await expect(page.locator("body")).not.toContainText("e2e-app-secret-not-real");
  });

  test("disconnecting returns the card to not connected", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await connectWithCredentials(page);
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
