import type { BrowserContext } from "@playwright/test";

import {
  GOOGLE_IDENTITY_ORIGIN,
  expect,
  installProviderIsolation,
  installSentinelGuard,
  test,
  type ProviderRequestRecord,
} from "./fixtures/provider-isolation";

/**
 * Regression for DP-P00-02 F-1: the sign-in surface must never fetch
 * Google's GIS script from the test runner, and the guard that prevents it
 * must be observable — a guard whose absence looks identical to its presence
 * proves nothing.
 *
 * Nothing in this file contacts Google. The negative control targets a path
 * on the test frontend itself, so "unguarded" is demonstrated by a request
 * that reaches a local server, not by one that leaves the machine.
 */

const GSI_SCRIPT = `${GOOGLE_IDENTITY_ORIGIN}/gsi/client`;

/**
 * The button only injects Google's script after the backend has minted a
 * nonce. Answering that one endpoint here lets the spec reach the script-load
 * step with or without a backend, so the regression is meaningful in a
 * hermetic local run and in CI alike. Context-level so a popup inherits it.
 * Only this path is answered; nothing else about the API is stubbed.
 */
async function stubGoogleNonce(context: BrowserContext): Promise<void> {
  await context.route("**/api/v1/auth/google/nonce", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ nonce: "provider-isolation-synthetic-nonce", expiresInSeconds: 300 }),
    }),
  );
}

function gsiEntries(log: ProviderRequestRecord[]): ProviderRequestRecord[] {
  return log.filter((entry) => entry.url.startsWith(GSI_SCRIPT));
}

test.describe("Provider isolation — Google identity", () => {
  test("the login page's GIS script request is fulfilled locally, not transmitted", async ({
    page,
    context,
    providerLog,
  }) => {
    await stubGoogleNonce(context);
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();

    // The button injects the script tag on mount; wait for the route layer to
    // have answered it rather than racing the assertion.
    await expect
      .poll(() => gsiEntries(providerLog).length, { timeout: 10_000 })
      .toBeGreaterThanOrEqual(1);

    for (const entry of gsiEntries(providerLog)) {
      expect(entry.action).toBe("fulfilled-local");
    }

    // The empty body loaded; the real library did not.
    const googleGlobal = await page.evaluate(
      () => typeof (window as unknown as { google?: unknown }).google,
    );
    expect(googleGlobal).toBe("undefined");

    // Ordinary same-origin traffic is not touched by the provider policy.
    expect(providerLog.every((entry) => entry.url.startsWith(GOOGLE_IDENTITY_ORIGIN))).toBe(true);
  });

  test("a popup opened from the page inherits the isolation policy", async ({
    page,
    context,
    providerLog,
  }) => {
    await stubGoogleNonce(context);
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await expect.poll(() => gsiEntries(providerLog).length).toBeGreaterThanOrEqual(1);
    const before = gsiEntries(providerLog).length;

    const popupPromise = page.waitForEvent("popup");
    await page.evaluate(() => window.open("/login", "_blank"));
    const popup = await popupPromise;
    await expect(popup.getByRole("heading", { name: "Welcome back" })).toBeVisible();

    // The popup's own script request must have been answered by the same
    // context-level route — a page-level route would not have covered it.
    await expect
      .poll(() => gsiEntries(providerLog).length, { timeout: 10_000 })
      .toBeGreaterThan(before);
    const popupGoogle = await popup.evaluate(
      () => typeof (window as unknown as { google?: unknown }).google,
    );
    expect(popupGoogle).toBe("undefined");
    await popup.close();
  });

  test("the security policy still permits the origin — isolation is a test-side route, not a CSP change", async ({
    page,
  }) => {
    const response = await page.goto("/login");
    const csp = response?.headers()["content-security-policy"] ?? "";
    expect(csp).toContain("accounts.google.com");
  });
});

test.describe("Provider isolation — negative control", () => {
  test("a guarded sentinel request is aborted inside the routing layer and recorded", async ({
    page,
    context,
    providerLog,
  }) => {
    await page.goto("/login");
    const origin = new URL(page.url()).origin;
    const sentinel = `${origin}/__provider_isolation_sentinel__`;
    await installSentinelGuard(context, sentinel, providerLog);

    const outcome = await page.evaluate(async (url) => {
      try {
        const res = await fetch(url);
        return { reached: true, status: res.status };
      } catch (error) {
        return { reached: false, error: String(error) };
      }
    }, sentinel);

    // Guard present: the request never reached the server.
    expect(outcome.reached).toBe(false);
    const recorded = providerLog.filter((entry) => entry.url.startsWith(sentinel));
    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.action).toBe("aborted");
  });

  test("without the guard the same sentinel request reaches the local server — so absence is detectable", async ({
    browser,
    baseURL,
  }) => {
    // A context created directly, bypassing the auto fixture, so we can show
    // what an *unguarded* request looks like — against our own server only.
    const context = await browser.newContext();
    const log: ProviderRequestRecord[] = [];
    // Google stays isolated even here; only the sentinel is left open.
    await installProviderIsolation(context, log);
    await stubGoogleNonce(context);
    const page = await context.newPage();
    try {
      await page.goto("/login");
      const origin = new URL(page.url()).origin;
      expect(origin).toBe(new URL(baseURL ?? origin).origin);
      const sentinel = `${origin}/__provider_isolation_sentinel__`;

      const outcome = await page.evaluate(async (url) => {
        const res = await fetch(url);
        return { reached: true, status: res.status };
      }, sentinel);

      // No guard: the request left the routing layer and hit the frontend,
      // which answers 404 for an unknown path. This is the failure path the
      // guard exists to prevent, exercised against a local sink.
      expect(outcome.reached).toBe(true);
      expect(outcome.status).toBe(404);
      expect(log.some((entry) => entry.url.startsWith(sentinel))).toBe(false);

      // And Google was still intercepted in this hand-made context. The script
      // request follows the nonce round-trip, so wait for the route layer to
      // have answered it rather than racing the assertion.
      await expect
        .poll(() => gsiEntries(log).length, { timeout: 10_000 })
        .toBeGreaterThanOrEqual(1);
      for (const entry of gsiEntries(log)) expect(entry.action).toBe("fulfilled-local");
    } finally {
      await context.close();
    }
  });
});
