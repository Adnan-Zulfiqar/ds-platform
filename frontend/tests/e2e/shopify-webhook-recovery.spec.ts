import { expect, test, type Page, type Route } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * Shopify integration card — connected-but-degraded webhook recovery.
 *
 * The defect this covers: a store whose webhook registration failed still
 * rendered as *Connected*, and the only recovery control was hidden precisely
 * because the status said `connected`. A merchant could see an amber note
 * telling them to reconnect, with no reconnect button and no retry — the only
 * way out was to disconnect a perfectly valid OAuth connection.
 *
 * **Mocked, and labelled as such.** The Shopify *status* and *reconcile*
 * responses are fulfilled by `page.route`, because reaching a genuinely
 * degraded connected store needs a live Shopify Partner app and a real access
 * token, neither of which exists here (see docs/TECHNICAL_DEBT.md M17). What is
 * real: the account, the sign-in, the React Query cache behaviour, the routing,
 * the rendering and every assertion about what the browser actually did. What
 * is stubbed is only the two API payloads.
 *
 * Backend behaviour for the same flow is exercised for real against PostgreSQL
 * in `backend/tests/integration/test_shopify_gql2_webhook_recovery.py`, and the
 * freshness invariant behind the "last confirmed" wording in
 * `test_shopify_gql2_webhook_freshness.py`.
 */

const STATUS_ROUTE = "**/api/v1/integrations/shopify/status";
const RECONCILE_ROUTE =
  "**/api/v1/integrations/shopify/stores/*/webhooks/reconcile";
const STORE_ID = "11111111-2222-3333-4444-555555555555";

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run Shopify webhook recovery tests.",
  );
});

type Health = "healthy" | "degraded" | "not_applicable";

// Three instants that render as three *different* strings at minute
// precision, in any timezone. The card shows "Connected", "Webhooks last
// confirmed" and the retry's new timestamp, so fixtures that collide at minute
// precision make "the old date is gone" assert against the wrong line — which
// is exactly what happened before these were spread out.
const CONNECTED_AT = "2026-08-18T08:15:00Z";
const FIRST_CONFIRMATION = "2026-08-19T11:40:05Z";
const SECOND_CONFIRMATION = "2026-08-20T16:25:00Z";

function connection(health: Health, confirmedAt: string = FIRST_CONFIRMATION) {
  return {
    id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    storeId: STORE_ID,
    shopDomain: "recovery-demo.myshopify.com",
    status: "connected",
    scopes: "read_products,read_orders,read_inventory",
    connectedAt: CONNECTED_AT,
    lastSyncAt: null,
    lastError: null,
    // The server clears this the moment a new reconciliation starts, so a
    // degraded row never carries a previous confirmation (F-01b).
    webhooksRegisteredAt: health === "healthy" ? confirmedAt : null,
    webhookHealth: health,
  };
}

/**
 * How the card renders a confirmation date.
 *
 * Evaluated **in the page**, not in Node: `toLocaleString` resolves against the
 * host's timezone and locale, and the browser's need not match the test
 * runner's. Computing it here rather than there made every date assertion fail
 * by exactly one hour on a machine off UTC — the kind of difference that passes
 * in CI and fails on a developer's laptop.
 */
async function displayed(page: Page, iso: string): Promise<string> {
  return page.evaluate(
    (value) =>
      new Date(value).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }),
    iso,
  );
}

interface StatusStub {
  /** How many times the card asked the server for authoritative status. */
  calls: number;
  /**
   * Change what the server would now say.
   *
   * A real successful retry changes the persisted `webhooksRegisteredAt`, so
   * the card must be re-reading the server rather than trusting the mutation
   * response — flipping this is how the test tells the difference.
   */
  setHealth: (next: Health, confirmedAt?: string) => void;
}

async function stubStatus(
  page: Page,
  health: Health,
  confirmedAt: string = FIRST_CONFIRMATION,
): Promise<StatusStub> {
  let current = health;
  let currentAt = confirmedAt;
  const stub: StatusStub = {
    calls: 0,
    setHealth: (next: Health, at?: string) => {
      current = next;
      if (at) currentAt = at;
    },
  };
  await page.route(STATUS_ROUTE, async (route: Route) => {
    stub.calls += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        connections: [connection(current, currentAt)],
      }),
    });
  });
  return stub;
}

/**
 * Report the session as a viewer.
 *
 * Both the restore path (`/auth/refresh`) and the explicit read (`/auth/me`)
 * carry the roles, and the provider seeds identity from whichever answers
 * first, so intercepting only one puts the owner roles straight back. There is
 * no endpoint that creates a second user with a viewer role in the same tenant,
 * so this covers the *UI* boundary only — the security boundary is covered by
 * the backend suite, which returns 403 to a viewer regardless of what the
 * interface renders.
 */
async function asViewer(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/refresh", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { identity: { roles: string[] } };
    await route.fulfill({
      response,
      json: { ...body, identity: { ...body.identity, roles: ["viewer"] } },
    });
  });
  await page.route("**/api/v1/auth/me", async (route) => {
    const response = await route.fetch();
    const identity = (await response.json()) as { roles: string[] };
    await route.fulfill({ response, json: { ...identity, roles: ["viewer"] } });
  });
}

function reconcileBody(healthy: boolean) {
  const topics = [
    "products/create",
    "products/update",
    "inventory_levels/update",
    "orders/create",
    "orders/updated",
    "app/uninstalled",
  ].map((topic) => ({
    topic,
    status: healthy ? "created" : "unknown",
    webhookGid: healthy ? "gid://shopify/WebhookSubscription/1" : null,
    detail: healthy ? null : "Shopify did not respond; the outcome is unknown.",
  }));
  return {
    storeId: STORE_ID,
    healthy,
    webhookHealth: healthy ? "healthy" : "degraded",
    topics,
    warnings: [],
    listedCount: 0,
    createdCount: healthy ? topics.length : 0,
    webhooksRegisteredAt: healthy ? SECOND_CONFIRMATION : null,
  };
}

async function stubReconcile(
  page: Page,
  outcome: "healthy" | "degraded" | "error",
): Promise<{ calls: number }> {
  const counter = { calls: 0 };
  await page.route(RECONCILE_ROUTE, async (route: Route) => {
    counter.calls += 1;
    if (outcome === "error") {
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "shopify_webhook_reconcile_busy",
          message:
            "Webhook setup for this store is already running. Please try again in a moment.",
          details: [],
          requestId: "e2e",
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(reconcileBody(outcome === "healthy")),
    });
  });
  return counter;
}

function shopifyRegion(page: Page) {
  return page.getByRole("region", { name: "Sales channels" });
}

function retryButton(page: Page) {
  return shopifyRegion(page).getByRole("button", {
    name: "Retry webhook setup",
  });
}

function statusRegion(page: Page) {
  return page.getByTestId(`shopify-webhook-status-${STORE_ID}`);
}

/**
 * The badge, not the timestamp line.
 *
 * Both read "Webhooks last confirmed" — the badge alone, the line followed by
 * a colon and the date — so an unqualified text match is ambiguous. `exact`
 * picks the badge; `confirmedLine` picks the line.
 */
function confirmedBadge(page: Page) {
  return shopifyRegion(page).getByText("Webhooks last confirmed", {
    exact: true,
  });
}

function confirmedLine(page: Page) {
  return shopifyRegion(page).getByText(/Webhooks last confirmed:/);
}

test.describe("Shopify card — connected but webhook-degraded (mocked API)", () => {
  test("a connected store with no webhook timestamp renders as degraded", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await page.goto("/settings/integrations");

    const shopify = shopifyRegion(page);
    await expect(
      shopify.getByText("recovery-demo.myshopify.com"),
    ).toBeVisible();
    await expect(shopify.getByText("Webhooks incomplete")).toBeVisible();
    await expect(
      shopify.getByText(/Product, inventory and order updates may be missed/i),
    ).toBeVisible();
    // The header summary must not claim a clean connection.
    await expect(shopify.getByText("1 needs webhook setup")).toBeVisible();
    await expect(shopify.getByText("1 connected")).toHaveCount(0);
  });

  test("the retry action is available without disconnecting", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await page.goto("/settings/integrations");

    await expect(retryButton(page)).toBeVisible();
    await expect(retryButton(page)).toBeEnabled();
    // The old escape hatch is still there, but it is no longer the only one.
    await expect(
      shopifyRegion(page).getByRole("button", { name: "Disconnect" }),
    ).toBeVisible();
  });

  test("no reconcile request is made on initial render", async ({ page }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    const reconcile = await stubReconcile(page, "healthy");
    await page.goto("/settings/integrations");

    await expect(retryButton(page)).toBeVisible();
    await page.waitForTimeout(1000);
    expect(reconcile.calls).toBe(0);
  });

  test("a successful retry moves the card to healthy and refetches status", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    const status = await stubStatus(page, "degraded");
    const reconcile = await stubReconcile(page, "healthy");
    await page.goto("/settings/integrations");
    await expect(retryButton(page)).toBeVisible();

    const before = status.calls;
    // The authoritative answer changes, exactly as a real successful retry
    // would make it change.
    status.setHealth("healthy", SECOND_CONFIRMATION);
    await retryButton(page).click();

    await expect(statusRegion(page)).toContainText(/Webhook setup confirmed/i);
    await expect(confirmedBadge(page)).toBeVisible();
    // The date is shown, because "confirmed" without a date is the claim this
    // wording exists to avoid making.
    await expect(confirmedLine(page)).toContainText(
      await displayed(page, SECOND_CONFIRMATION),
    );
    await expect(retryButton(page)).toHaveCount(0);
    expect(reconcile.calls).toBe(1);
    expect(status.calls).toBeGreaterThan(before);
  });

  test("a still-degraded retry keeps the warning and the retry action", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    const reconcile = await stubReconcile(page, "degraded");
    await page.goto("/settings/integrations");

    await retryButton(page).click();

    await expect(statusRegion(page)).toContainText(/still incomplete/i);
    await expect(
      shopifyRegion(page).getByText(
        /Product, inventory and order updates may be missed/i,
      ),
    ).toBeVisible();
    await expect(retryButton(page)).toBeVisible();
    // One click, one request — no self-retry loop.
    await page.waitForTimeout(1000);
    expect(reconcile.calls).toBe(1);
  });

  test("a rejected retry shows the failure and does not loop", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    const reconcile = await stubReconcile(page, "error");
    await page.goto("/settings/integrations");

    await retryButton(page).click();

    await expect(statusRegion(page)).toContainText(/already running/i);
    await expect(retryButton(page)).toBeEnabled();
    await page.waitForTimeout(1500);
    expect(reconcile.calls).toBe(1);
  });

  test("the retry is keyboard operable and moves focus to the outcome", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await stubReconcile(page, "healthy");
    await page.goto("/settings/integrations");

    const button = retryButton(page);
    await button.focus();
    await expect(button).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(statusRegion(page)).toContainText(/Webhook setup confirmed/i);
    // Focus lands on the answer rather than being lost or left on a control
    // that has now disappeared.
    await expect(statusRegion(page)).toBeFocused();
    await expect(statusRegion(page)).toHaveAttribute("aria-live", "polite");
    await expect(statusRegion(page)).toHaveAttribute("role", "status");
  });

  test("a healthy store shows no warning and no retry control", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "healthy");
    await page.goto("/settings/integrations");

    await expect(confirmedBadge(page)).toBeVisible();
    await expect(confirmedLine(page)).toContainText(
      await displayed(page, FIRST_CONFIRMATION),
    );
    // Never an unqualified claim about live provider state.
    await expect(shopifyRegion(page).getByText("Webhooks active")).toHaveCount(
      0,
    );
    await expect(retryButton(page)).toHaveCount(0);
    await expect(
      shopifyRegion(page).getByText(/updates may be missed/i),
    ).toHaveCount(0);
  });

  test("the degraded OAuth result is not shown as a plain success", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await page.goto(
      "/settings/integrations?shopify=connected_webhooks_degraded",
    );

    const banner = page.getByRole("alert").first();
    await expect(
      banner.getByRole("heading", { name: /webhook setup incomplete/i }),
    ).toBeVisible();
    await expect(banner.getByText(/may be missed/i)).toBeVisible();
    // The success banner's wording must not be what a merchant sees here.
    await expect(
      page.getByRole("heading", { name: "Shopify connected", exact: true }),
    ).toHaveCount(0);
  });

  test("the degraded card renders in dark mode and on a narrow viewport", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/settings/integrations");

    const warning = shopifyRegion(page).getByText(
      /Product, inventory and order updates may be missed/i,
    );
    await expect(warning).toBeVisible();
    await expect(retryButton(page)).toBeVisible();

    // The control must stay inside the viewport rather than overflowing it,
    // which is how a recovery action becomes unreachable on a phone.
    const box = await retryButton(page).boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  });

  test("a viewer sees a read-only explanation and no retry control", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await asViewer(page);
    await stubStatus(page, "degraded");
    const reconcile = await stubReconcile(page, "healthy");
    // A full navigation, so the auth provider re-reads identity through the
    // routes above rather than keeping the owner roles it already has.
    await page.goto("/settings/integrations");

    const shopify = shopifyRegion(page);
    // The warning is still shown — hiding the problem from a viewer would be
    // worse than hiding the button.
    await expect(
      shopify.getByText(/Product, inventory and order updates may be missed/i),
    ).toBeVisible();
    await expect(shopify.getByText("Read only")).toBeVisible();
    await expect(shopify.getByText(/Ask an administrator/i)).toBeVisible();

    await expect(retryButton(page)).toHaveCount(0);
    await expect(
      shopify.getByRole("button", { name: "Disconnect" }),
    ).toHaveCount(0);
    expect(reconcile.calls).toBe(0);
  });

  test("no unexpected console errors while recovering", async ({ page }) => {
    // Listening starts *after* sign-in on purpose. The session-restore probe
    // legitimately 401s for an anonymous first load, and asserting on that
    // would make this test about the auth bootstrap rather than about the
    // recovery flow it is named for.
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await stubReconcile(page, "healthy");

    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(String(error)));

    await page.goto("/settings/integrations");
    await retryButton(page).click();
    await expect(statusRegion(page)).toContainText(/confirmed/i);

    expect(errors).toEqual([]);
  });
});

/**
 * F-01b — a confirmation must describe the most recent attempt.
 *
 * Before this fix, `webhooksRegisteredAt` was only ever written. A store that
 * reconciled successfully once kept that timestamp through every later failure,
 * so the card rendered a healthy state for a store whose subscriptions were
 * gone — and, because Retry only appears while degraded, the stale timestamp
 * also removed the way out.
 *
 * The server now clears the timestamp before it contacts Shopify, so these
 * tests drive the card with the payloads that change actually produces.
 */
test.describe("Shopify card — a stale confirmation cannot persist (mocked API)", () => {
  test("a previously confirmed store that fails reconciliation renders degraded", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    const status = await stubStatus(page, "healthy");
    await page.goto("/settings/integrations");

    await expect(confirmedBadge(page)).toBeVisible();
    await expect(confirmedLine(page)).toContainText(
      await displayed(page, FIRST_CONFIRMATION),
    );

    // What the server does when a later reconciliation fails: it clears the
    // confirmation before contacting Shopify, so the next authoritative read
    // carries no date at all.
    status.setHealth("degraded");
    await page.goto("/settings/integrations");

    await expect(
      shopifyRegion(page).getByText("Webhooks incomplete"),
    ).toBeVisible();
    await expect(confirmedBadge(page)).toHaveCount(0);
    await expect(confirmedLine(page)).toHaveCount(0);
    await expect(
      shopifyRegion(page).getByText(await displayed(page, FIRST_CONFIRMATION)),
    ).toHaveCount(0);
    await expect(
      shopifyRegion(page).getByText(
        /Product, inventory and order updates may be missed/i,
      ),
    ).toBeVisible();
  });

  test("the retry action stays available after a failed retry on a once-healthy store", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    const status = await stubStatus(page, "healthy");
    const reconcile = await stubReconcile(page, "degraded");
    await page.goto("/settings/integrations");
    await expect(confirmedBadge(page)).toBeVisible();

    // The store degrades, the merchant reloads, and Retry is now offered.
    status.setHealth("degraded");
    await page.goto("/settings/integrations");
    await retryButton(page).click();
    await expect(statusRegion(page)).toContainText(/still incomplete/i);

    // The merchant is not stranded: the control that got them here is still
    // there, and it has not fired again on its own.
    await expect(retryButton(page)).toBeVisible();
    await expect(retryButton(page)).toBeEnabled();
    await page.waitForTimeout(1000);
    expect(reconcile.calls).toBe(1);
  });

  test("a degraded row never carries a previous confirmation date", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await page.goto("/settings/integrations");

    const shopify = shopifyRegion(page);
    await expect(shopify.getByText("Webhooks incomplete")).toBeVisible();
    await expect(confirmedBadge(page)).toHaveCount(0);
    await expect(confirmedLine(page)).toHaveCount(0);
    await expect(
      shopify.getByText(await displayed(page, FIRST_CONFIRMATION)),
    ).toHaveCount(0);
  });

  test("a degraded reconnect result cannot show the previous healthy confirmation", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await page.goto(
      "/settings/integrations?shopify=connected_webhooks_degraded",
    );

    const banner = page.getByRole("alert").first();
    await expect(
      banner.getByRole("heading", { name: /webhook setup incomplete/i }),
    ).toBeVisible();
    await expect(confirmedBadge(page)).toHaveCount(0);
    await expect(confirmedLine(page)).toHaveCount(0);
    await expect(retryButton(page)).toBeVisible();
  });

  test("recovering re-confirms with the new date, not the old one", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    const status = await stubStatus(page, "degraded");
    await stubReconcile(page, "healthy");
    await page.goto("/settings/integrations");
    // Wait for the degraded card before changing what the server would say --
    // flipping first can beat the first status fetch, and the card then renders
    // healthy with no Retry control to click.
    await expect(retryButton(page)).toBeVisible();

    status.setHealth("healthy", SECOND_CONFIRMATION);
    await retryButton(page).click();

    await expect(confirmedLine(page)).toContainText(
      await displayed(page, SECOND_CONFIRMATION),
    );
    await expect(
      shopifyRegion(page).getByText(await displayed(page, FIRST_CONFIRMATION)),
    ).toHaveCount(0);
  });

  test("a once-healthy degraded store still renders in dark mode on mobile", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "degraded");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/settings/integrations");

    await expect(retryButton(page)).toBeVisible();
    const box = await retryButton(page).boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  });

  test("a viewer sees the degraded explanation but never the control", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await asViewer(page);
    await stubStatus(page, "degraded");
    const reconcile = await stubReconcile(page, "healthy");
    await page.goto("/settings/integrations");

    await expect(
      shopifyRegion(page).getByText(
        /Product, inventory and order updates may be missed/i,
      ),
    ).toBeVisible();
    await expect(retryButton(page)).toHaveCount(0);
    expect(reconcile.calls).toBe(0);
  });

  test("no reconcile request is made when a healthy store is merely viewed", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await stubStatus(page, "healthy");
    const reconcile = await stubReconcile(page, "healthy");
    await page.goto("/settings/integrations");

    await expect(confirmedBadge(page)).toBeVisible();
    await page.waitForTimeout(1000);
    // Reading must never invalidate: a page view that reconciled would clear a
    // good confirmation for as long as the round trip took.
    expect(reconcile.calls).toBe(0);
  });
});
