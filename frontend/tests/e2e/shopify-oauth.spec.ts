import { expect, test, type Page, type Request } from "@playwright/test";

import { API_URL, isApiReachable, isRedisAvailable, registerAndSignIn } from "./helpers/auth";

/**
 * Shopify OAuth connect flow (AutoDS-style: store domain only, never a
 * merchant-supplied API key/secret/token).
 *
 * What this suite can and cannot prove without a live Shopify Partner app:
 *
 * - The merchant-facing dialog, domain normalisation, and the start-of-connect
 *   round trip to our own backend are exercised for real (registered account,
 *   real HTTP call, real `ShopifyInvalidShopError`/`ShopifyConfigError`
 *   handling). The external navigation to Shopify's own consent screen is
 *   intercepted and fulfilled with a stub page — following the live consent
 *   screen would leave the app and requires a real Shopify store, which is
 *   unavailable here (see docs/TECHNICAL_DEBT.md M17).
 * - Assertions for the OAuth hand-off use the *outgoing request URL* and
 *   `waitForURL`, never a connect-response body after navigation begins.
 * - The callback banners are exercised by navigating with the query parameter
 *   the backend redirect would produce.
 * - Connected / disconnect / reconnect against a *real* connected store are
 *   NOT exercised here (needs live Partner credentials — M17).
 */

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run Shopify OAuth tests.",
  );
});

function shopifyRegion(page: Page) {
  return page.getByRole("region", { name: "Sales channels" });
}

async function openConnectDialog(page: Page): Promise<void> {
  const shopify = shopifyRegion(page);
  await shopify.getByRole("button", { name: "Connect Shopify" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
}

/** Stub Shopify's consent screen so the browser never leaves our control. */
async function stubShopifyAuthorizeScreen(page: Page): Promise<void> {
  await page.route("**/admin/oauth/authorize*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>oauth-blocked</title><body>oauth-blocked</body>",
    });
  });
}

function isAuthorizeRequestForShop(request: Request, shopHandle: string): boolean {
  try {
    const url = new URL(request.url());
    return (
      url.hostname === `${shopHandle}.myshopify.com` &&
      url.pathname.includes("/admin/oauth/authorize")
    );
  } catch {
    return false;
  }
}

function assertShopifyAuthorizeUrl(rawUrl: string, shopHandle: string): void {
  const url = new URL(rawUrl);
  expect(url.protocol).toBe("https:");
  expect(url.hostname).toBe(`${shopHandle}.myshopify.com`);
  expect(url.pathname).toBe("/admin/oauth/authorize");
  expect(url.searchParams.get("client_id")).toBeTruthy();
  expect(url.searchParams.get("scope")).toBeTruthy();
  expect(url.searchParams.get("redirect_uri")).toBeTruthy();
  expect(url.searchParams.get("state")).toBeTruthy();
  expect(url.searchParams.get("redirect_uri")).toMatch(/\/api\/v1\/integrations\/shopify\/callback/);
}

test.describe("Shopify connect form", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("asks only for a store domain — no API key, secret, or token field", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const shopify = shopifyRegion(page);
    await expect(shopify.getByRole("button", { name: "Connect Shopify" })).toBeVisible();
    await openConnectDialog(page);

    const dialog = page.getByRole("dialog");
    await expect(dialog.getByLabel("Store domain")).toBeVisible();
    for (const label of ["API key", "Client secret", "Access token", "Admin token", "App secret"]) {
      await expect(page.getByLabel(label)).toHaveCount(0);
    }
  });

  test("rejects a custom storefront domain without contacting Shopify", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await openConnectDialog(page);
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Store domain").fill("my-store.com");
    await dialog.getByRole("button", { name: "Continue to Shopify" }).click();

    await expect(dialog.getByText(/is not a Shopify admin domain/i)).toBeVisible();
  });
});

const domainForms: Array<{ label: string; toInput: (shop: string) => string }> = [
  { label: "bare handle", toInput: (shop) => shop },
  { label: "myshopify.com", toInput: (shop) => `${shop}.myshopify.com` },
  { label: "https:// prefixed", toInput: (shop) => `https://${shop}.myshopify.com` },
];

test.describe("Shopify connect domain normalisation", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const { label, toInput } of domainForms) {
    test(`normalises a ${label} domain onto the Shopify authorize URL`, async ({ page }) => {
      test.skip(
        !(await isRedisAvailable()),
        "Redis is not available — OAuth state storage is required to begin a connection.",
      );

      const shop = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

      await stubShopifyAuthorizeScreen(page);
      await registerAndSignIn(page);
      await page.goto("/settings/integrations");

      await openConnectDialog(page);
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Store domain").fill(toInput(shop));

      const connectResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/integrations/shopify/connect") &&
          response.request().method() === "POST",
      );
      const authorizeRequestPromise = page.waitForRequest((request) =>
        isAuthorizeRequestForShop(request, shop),
      );

      await dialog.getByRole("button", { name: "Continue to Shopify" }).click();

      const [connectResponse, authorizeRequest] = await Promise.all([
        connectResponsePromise,
        authorizeRequestPromise,
      ]);

      expect(connectResponse.status()).toBe(201);
      assertShopifyAuthorizeUrl(authorizeRequest.url(), shop);

      await page.waitForURL((url) => url.hostname === `${shop}.myshopify.com`);
      assertShopifyAuthorizeUrl(page.url(), shop);
    });
  }
});

test.describe("Shopify OAuth callback banners", () => {
  const cases: Array<{ query: string; heading: string; bodyPattern: RegExp }> = [
    { query: "connected", heading: "Shopify connected", bodyPattern: /now linked/i },
    {
      query: "denied",
      heading: "Authorization declined",
      bodyPattern: /declined on Shopify/i,
    },
    {
      query: "hmac",
      heading: "Shopify signature invalid",
      bodyPattern: /app secret/i,
    },
    {
      query: "state",
      heading: "Authorization expired",
      bodyPattern: /expired or was reused/i,
    },
    {
      query: "exchange",
      heading: "Token exchange failed",
      bodyPattern: /rejected the code exchange/i,
    },
    {
      query: "failed",
      heading: "Connection failed",
      bodyPattern: /\*\.myshopify\.com/i,
    },
    {
      query: "taken",
      heading: "Store already linked",
      bodyPattern: /another DropPilot workspace/i,
    },
  ];

  for (const { query, heading, bodyPattern } of cases) {
    test(`renders the "${query}" outcome`, async ({ page }) => {
      await registerAndSignIn(page);
      await page.goto(`/settings/integrations?shopify=${query}`);

      const banner = page.getByRole("alert");
      await expect(banner.getByRole("heading", { name: heading })).toBeVisible();
      await expect(banner.getByText(bodyPattern)).toBeVisible();
    });
  }
});

test.describe("Shopify OAuth callback — malformed request handling", () => {
  test("a callback with no valid signature is rejected, not crashed", async ({ page }) => {
    const zeroHmac = "0".repeat(64);
    await page.goto(
      `${API_URL}/api/v1/integrations/shopify/callback?code=fake&state=does-not-exist&shop=e2e-malformed.myshopify.com&hmac=${zeroHmac}`,
    );

    await expect(page).toHaveURL(/\/settings\/integrations\?shopify=hmac/);
  });
});
