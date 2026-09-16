import { expect, test as base, type BrowserContext } from "@playwright/test";

/**
 * Provider network isolation for E2E tests that render the sign-in surface.
 *
 * With a synthetic Google client id configured (as CI does), `/login` and
 * `/register` mount the GIS button. Once its `POST /auth/google/nonce`
 * succeeds — which it does whenever the backend is up, as in CI — the button
 * injects `<script src="https://accounts.google.com/gsi/client">`. Any spec
 * that visits those pages without routing that origin then makes a real
 * request to Google from the test runner — DP-P00-02 finding F-1. (Without
 * a backend the nonce call fails first and Google is never contacted, which
 * is why the leak was invisible in API-less local runs.) Per-spec `page.route`
 * calls are easy to forget; this fixture installs the policy on the
 * **browser context**, so every page and popup in the test inherits it
 * before the first navigation.
 *
 * The route *fulfils* locally with an empty script rather than aborting:
 * an abort raises `net::ERR_FAILED` in the console, which the console-clean
 * assertions elsewhere would count as an error. An empty body loads
 * successfully, leaves `window.google` undefined, and the button settles on
 * its "unavailable" state. Specs that are *about* Google sign-in
 * (`auth-g1.spec.ts`) keep their own page-level stub, which Playwright
 * evaluates before context routes, so their coverage is unchanged.
 *
 * Every provider request the fixture handles is recorded with the action
 * the route layer took. That classification — not `page.on("request")`,
 * which fires for intercepted requests too — is what the regression spec
 * asserts on. A recorded `fulfilled-local`/`aborted` entry means the request
 * was answered inside the browser's routing layer and never reached the
 * network stack; nothing here measures socket egress directly.
 */

export type ProviderRouteAction = "fulfilled-local" | "aborted";

export interface ProviderRequestRecord {
  url: string;
  action: ProviderRouteAction;
}

export const GOOGLE_IDENTITY_ORIGIN = "https://accounts.google.com";

const EMPTY_SCRIPT = {
  status: 200,
  contentType: "application/javascript",
  body: "",
} as const;

/**
 * Install the Google identity route on a context. Exported so the regression
 * spec can install it on a context it creates itself and reason about timing.
 */
export async function installProviderIsolation(
  context: BrowserContext,
  log: ProviderRequestRecord[],
): Promise<void> {
  await context.route(`${GOOGLE_IDENTITY_ORIGIN}/**`, async (route) => {
    log.push({ url: route.request().url(), action: "fulfilled-local" });
    await route.fulfill(EMPTY_SCRIPT);
  });
}

/**
 * Install an abort-and-record guard for one exact sentinel URL. Used only by
 * the regression spec's negative control; never applied to real provider
 * hosts. Exact rather than `origin/**` so the guard cannot swallow ordinary
 * same-origin traffic of the page under test.
 */
export async function installSentinelGuard(
  context: BrowserContext,
  sentinelUrl: string,
  log: ProviderRequestRecord[],
): Promise<void> {
  await context.route(sentinelUrl, async (route) => {
    log.push({ url: route.request().url(), action: "aborted" });
    await route.abort("blockedbyclient");
  });
}

type ProviderIsolationFixtures = {
  /** Requests the isolation policy handled during this test, in order. */
  providerLog: ProviderRequestRecord[];
};

export const test = base.extend<ProviderIsolationFixtures>({
  providerLog: [
    async ({ context }, use) => {
      const log: ProviderRequestRecord[] = [];
      await installProviderIsolation(context, log);
      await use(log);
    },
    // `auto` so importing this `test` is sufficient — no spec has to remember
    // to request the fixture for the route to be installed.
    { auto: true },
  ],
});

export { expect };
