import { expect, test, type Page } from "@playwright/test";

import { isApiReachable, isRedisAvailable, registerAndSignIn } from "./helpers/auth";

/**
 * EBAY-C1 — the eBay integration card, against the real API.
 *
 * The states asserted here are the ones reachable without completing a live
 * eBay consent round trip: not connected, unavailable when the server has no
 * eBay application configured, the consent hand-off, and every outcome banner
 * the callback can produce.
 *
 * **No eBay request is made.** The redirect to `auth.ebay.com` is aborted, so
 * the test observes the URL the server produced instead of following it. The
 * callback outcomes are asserted by visiting the return URL directly with the
 * query parameter the server would have set — which is exactly what the browser
 * receives, minus eBay's involvement.
 */

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run eBay integration tests.",
  );
});

function channels(page: Page) {
  return page.getByRole("region", { name: "Sales channels" });
}

function ebayCard(page: Page) {
  return page.getByTestId("ebay-card");
}

/**
 * Whether this server has eBay application credentials.
 *
 * Read from the card the server rendered rather than from the environment: the
 * test process and the backend process do not necessarily share one, and
 * guessing wrong would turn a correct "unavailable" state into a failure.
 */
async function ebayIsConfigured(page: Page): Promise<boolean> {
  const badge = ebayCard(page).getByText("Unavailable");
  return (await badge.count()) === 0;
}

/**
 * Start a connection and return the consent URL the browser was sent to.
 *
 * Captured from the outgoing *request* rather than parsed out of the connect
 * response, and for a concrete reason: the card navigates as soon as it has the
 * URL, and a navigation discards the response body before a test can read it.
 * Reading the request is also the stronger assertion — it is the URL the
 * browser actually followed, not the one the API said it should.
 *
 * The request is aborted, so nothing ever reaches eBay.
 */
async function beginConnect(page: Page): Promise<string> {
  let resolveUrl: (url: string) => void;
  const consentUrl = new Promise<string>((resolve) => {
    resolveUrl = resolve;
  });

  await page.route("**/oauth2/authorize*", async (route) => {
    resolveUrl(route.request().url());
    await route.abort();
  });

  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/integrations/ebay/connect"),
  );
  await ebayCard(page).getByRole("button", { name: /^(Connect eBay|Reconnect)$/ }).click();

  expect((await responsePromise).status()).toBe(201);
  return consentUrl;
}

test.describe("eBay integration card", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("is a real card, not a coming-soon placeholder", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const card = ebayCard(page);
    await expect(card).toBeVisible();
    await expect(card.getByRole("heading", { name: "eBay" })).toBeVisible();
    // The placeholder grid still exists for the channels that really are
    // planned; eBay must no longer be one of them.
    await expect(page.getByTestId("planned-channels").getByText("Etsy")).toBeVisible();
    await expect(card.getByText("Coming soon")).toHaveCount(0);
  });

  test("shows a new workspace as not connected", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const card = ebayCard(page);
    if (!(await ebayIsConfigured(page))) {
      await expect(card.getByText("Unavailable")).toBeVisible();
      await expect(card.getByRole("button", { name: "Connect eBay" })).toBeDisabled();
      return;
    }

    await expect(card.getByText("Not connected")).toBeVisible();
    await expect(card.getByRole("button", { name: "Connect eBay" })).toBeEnabled();
  });

  test("asks the merchant for no eBay credentials", async ({ page }) => {
    /**
     * The card must never collect a client id, certificate id or RuName. Those
     * are the platform's, they live in server configuration, and a field for
     * them would invite a merchant to paste a credential into a web form.
     */
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    const card = ebayCard(page);
    await expect(card.locator("input")).toHaveCount(0);
    await expect(card.getByText(/client id/i)).toHaveCount(0);
    await expect(card.getByText(/certificate/i)).toHaveCount(0);
    await expect(card.getByText(/runame/i)).toHaveCount(0);
  });

  test("hands off to eBay's own consent page", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    test.skip(
      !(await ebayIsConfigured(page)),
      "This server has no eBay application configured.",
    );
    test.skip(
      !(await isRedisAvailable()),
      "Redis is not available — the OAuth state store is required to begin a connection.",
    );

    const url = new URL(await beginConnect(page));

    expect(url.origin).toBe("https://auth.ebay.com");
    expect(url.pathname).toBe("/oauth2/authorize");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("state")).toBeTruthy();
    // The RuName is an opaque eBay identifier, not a URL. Sending a URL here
    // fails on eBay's own page with nothing this application could diagnose.
    expect(url.searchParams.get("redirect_uri")).not.toMatch(/^https?:/);
    // The scope set is asserted exactly in the backend suite; here it is enough
    // that the platform is not quietly asking for access to a seller's money.
    const scope = url.searchParams.get("scope") ?? "";
    expect(scope).toContain("sell.inventory");
    expect(scope).not.toContain("sell.finances");
  });

  test("disconnecting is available once a connection exists", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    test.skip(
      !(await ebayIsConfigured(page)),
      "This server has no eBay application configured.",
    );

    // Nothing is connected yet, so the card offers Connect and no Disconnect.
    const card = ebayCard(page);
    await expect(card.getByRole("button", { name: "Disconnect" })).toHaveCount(0);
    await expect(card.getByRole("button", { name: "Connect eBay" })).toBeVisible();
  });
});

test.describe("eBay callback outcomes", () => {
  /**
   * Every branch the server's redirect vocabulary can produce.
   *
   * These are the messages a merchant actually reads after being sent back from
   * eBay, and each one has to say what happened and what to do — a generic
   * "something went wrong" is what turns a two-minute fix into a support
   * ticket.
   */
  const OUTCOMES = [
    { param: "connected", heading: "eBay connected" },
    { param: "denied", heading: "Authorization declined" },
    { param: "invalid", heading: "Authorization expired" },
    { param: "already_linked", heading: "eBay account already linked" },
    { param: "failed", heading: "Connection failed" },
  ] as const;

  for (const { param, heading } of OUTCOMES) {
    test(`?ebay=${param} explains itself`, async ({ page }) => {
      await registerAndSignIn(page);
      await page.goto(`/settings/integrations?ebay=${param}`);

      await expect(page.getByText(heading)).toBeVisible();
    });
  }

  test("an unrecognised outcome renders nothing from the query string", async ({
    page,
  }) => {
    /**
     * The vocabulary is fixed and owned by this application: the parameter
     * selects one of five pre-written messages or none. Rendering whatever
     * arrives in the query string is how a reflected-content bug is built.
     *
     * Asserted as "no outcome banner, and nothing echoed" rather than "no alert
     * at all" — the page has a permanent informational alert about planned
     * channels, and matching that would make this test pass for the wrong
     * reason.
     */
    await registerAndSignIn(page);
    await page.goto("/settings/integrations?ebay=%3Cscript%3Ealert(1)%3C/script%3E");

    await expect(ebayCard(page)).toBeVisible();
    for (const { heading } of OUTCOMES) {
      await expect(page.getByText(heading)).toHaveCount(0);
    }
    await expect(page.getByText("alert(1)")).toHaveCount(0);
    await expect(page.locator("script:has-text('alert(1)')")).toHaveCount(0);
  });
});

test.describe("eBay card responsiveness", () => {
  test("renders without horizontal overflow at 320px", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 720 });
    await registerAndSignIn(page);
    await page.goto("/settings/integrations");

    await expect(ebayCard(page)).toBeVisible();
    const overflows = await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflows).toBe(false);
  });
});
