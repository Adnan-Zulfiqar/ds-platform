import { expect, test, type Page, type Request } from "@playwright/test";

import { API_URL, isApiReachable, isRedisAvailable, registerAndSignIn } from "./helpers/auth";

/**
 * Shopify OAuth connect flow (AutoDS-style: store domain only, never a
 * merchant-supplied API key/secret/token).
 *
 * What this suite can and cannot prove without a live Shopify Partner app:
 *
 * - The merchant-facing form, domain normalisation, and the start-of-connect
 *   round trip to our own backend are exercised for real (registered account,
 *   real HTTP call, real `ShopifyInvalidShopError`/`ShopifyConfigError`
 *   handling). The external navigation to Shopify's own consent screen is
 *   intercepted and fulfilled with a stub page — following the live consent
 *   screen would leave the app and requires a real Shopify store, which is
 *   unavailable here (see docs/TECHNICAL_DEBT.md M17).
 * - Assertions for the OAuth hand-off use the *outgoing request URL* and
 *   `waitForURL`, never a connect-response body after navigation begins.
 *   Reading `.json()` / `.text()` after `window.location.assign` races
 *   Chromium discarding the response ("execution context destroyed" /
 *   aborted response).
 * - The callback banners (`connected`/`denied`/`hmac`/`state`/`exchange`/
 *   `failed`) are exercised by navigating directly with the query parameter
 *   the backend redirect would produce — this proves the frontend renders
 *   each reason correctly, not that the backend classifies a *live* Shopify
 *   callback into that reason. That classification (HMAC verification, state
 *   expiry/reuse, shop-domain mismatch) has its own backend unit/integration
 *   coverage (`test_shopify_auth.py`, `test_integrations.py`) run against
 *   synthetic — not live — Shopify requests, for the same reason.
 * - Connected / disconnect / reconnect against a *real* connected store are
 *   NOT exercised here. Reaching that state requires either a completed
 *   Shopify OAuth round trip (needs a real Partner app + store) or a
 *   test-only endpoint that fabricates a connection — which does not exist,
 *   deliberately, since an endpoint like that would be a standing security
 *   hole. This is the one part of the spec's Playwright list left
 *   unverified; it stays open pending real Shopify Partner credentials
 *   (M17), same as the rest of the live-Shopify gap already on record.
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

/** Stub Shopify's consent screen so the browser never leaves our control. */
async function stubShopifyAuthorizeScreen(page: Page): Promise<void> {
  // Fulfill, do not abort: aborting a top-level navigation leaves a pending
  // Chrome navigation error that races the next assertion / page lifecycle.
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
    await expect(shopify.getByLabel("Store domain")).toBeVisible();
    for (const label of ["API key", "Client secret", "Access token", "Admin token", "App secret"]) {
      await expect(shopify.getByLabel(label)).toHaveCount(0);
    }
    // Not a modal at all — the form is inline in the Sales channels card.
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("rejects a custom storefront domain without contacting Shopify", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const shopify = shopifyRegion(page);
    await shopify.getByLabel("Store domain").fill("my-store.com");
    await shopify.getByRole("button", { name: "Connect Shopify" }).click();

    // Assert the validation error specifically — `/myshopify\.com/i` also matches
    // the static `*.myshopify.com` hint in the same card (strict-mode collision).
    await expect(shopify.getByText(/is not a Shopify admin domain/i)).toBeVisible();
  });
});

/**
 * Each input form is its own test with a fresh `page` fixture. Sharing a page
 * across a loop left an aborted Shopify navigation racing the next iteration.
 */
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

      const shopifyCard = shopifyRegion(page);
      await shopifyCard.getByLabel("Store domain").fill(toInput(shop));

      // Arm listeners before the click. Assert on status (not body) and on the
      // outbound authorize request — never `.json()` after navigation starts.
      const connectResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/integrations/shopify/connect") &&
          response.request().method() === "POST",
      );
      const authorizeRequestPromise = page.waitForRequest((request) =>
        isAuthorizeRequestForShop(request, shop),
      );

      await shopifyCard.getByRole("button", { name: "Connect Shopify" }).click();

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
      bodyPattern: /SHOPIFY_API_SECRET/,
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
      bodyPattern: /Allowed redirection URL/i,
    },
  ];

  for (const { query, heading, bodyPattern } of cases) {
    test(`renders the "${query}" outcome`, async ({ page }) => {
      await registerAndSignIn(page);
      await page.goto(`/settings/integrations?shopify=${query}`);

      const banner = page.getByRole("alert");
      await expect(banner.getByRole("heading", { name: heading })).toBeVisible();
      // Scope to the alert: the Shopify card helper copy also mentions
      // "Allowed redirection URL", which collides under strict mode.
      await expect(banner.getByText(bodyPattern)).toBeVisible();
    });
  }
});

test.describe("Shopify OAuth callback — malformed request handling", () => {
  test("a callback with no valid signature is rejected, not crashed", async ({ page }) => {
    // Simulates the shape of an invalid/forged OAuth redirect — this cannot
    // carry a valid Shopify HMAC without a live store's app secret, so it
    // exercises the same rejection path a tampered or replayed callback
    // would hit. HMAC is verified before the state token is even read, so
    // this deterministically hits the "hmac" reason, not "state" — it does
    // not independently prove state-expiry/reuse handling in the browser;
    // see the suite-level comment above for what does.
    const zeroHmac = "0".repeat(64);
    await page.goto(
      `${API_URL}/api/v1/integrations/shopify/callback?code=fake&state=does-not-exist&shop=e2e-malformed.myshopify.com&hmac=${zeroHmac}`,
    );

    await expect(page).toHaveURL(/\/settings\/integrations\?shopify=hmac/);
  });
});
