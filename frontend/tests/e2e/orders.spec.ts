import { expect, test } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * Orders page tests.
 *
 * Run against the real API, like the product suite. What these deliberately do
 * not cover: a list populated with real supplier orders. Orders cannot be
 * seeded through the platform's own API (there is no manual-create endpoint by
 * design — orders only enter via synchronisation), and the live AliExpress
 * account has no orders to import. Populated rendering is covered by the
 * backend integration suite against captured payload shapes; what the browser
 * suite proves is the page contract: states, dialog, filters, and protection.
 */

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run order tests.",
  );
});

test.describe("Orders page", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("is reachable from the sidebar", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/dashboard");

    await page
      .getByRole("navigation")
      .first()
      .getByRole("link", { name: "Orders" })
      .click();

    await expect(page).toHaveURL(/\/orders$/);
    await expect(
      page.getByRole("heading", { name: "Orders", level: 1 }),
    ).toBeVisible();
  });

  test("shows live statistics and an empty state for a new workspace", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/orders");

    // The statistics row is real data: a fresh tenant has zero everywhere.
    const statistics = page.getByTestId("order-statistics");
    await expect(statistics.getByText("Orders synced")).toBeVisible();
    await expect(statistics.getByText("Last synchronisation")).toBeVisible();
    await expect(statistics.getByText("Never")).toBeVisible();

    // An empty state, not an error: a new workspace legitimately has no
    // orders, and the next useful step is a synchronisation.
    await expect(page.getByText("No orders yet")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Sync orders" }).first(),
    ).toBeVisible();
  });

  test("the sync dialog validates the window without calling the server", async ({
    page,
  }) => {
    await registerAndSignIn(page);
    await page.goto("/orders");

    let requested = false;
    await page.route("**/orders/sync", (route) => {
      requested = true;
      return route.abort();
    });

    await page.getByRole("button", { name: "Sync orders" }).first().click();
    await page.getByLabel("Window (days)").fill("500");
    await page.getByRole("button", { name: "Sync now" }).click();

    await expect(
      page.getByText("Enter a window between 1 and 90 days."),
    ).toBeVisible();
    expect(requested).toBe(false);
  });

  test("surfaces the server's reason when no supplier is connected", async ({
    page,
  }) => {
    /**
     * The first failure a real user meets: they press sync before connecting
     * AliExpress. The dialog must relay the server's explanation rather than
     * failing silently.
     */
    await registerAndSignIn(page);
    await page.goto("/orders");

    await page.getByRole("button", { name: "Sync orders" }).first().click();

    const response = page.waitForResponse((r) => r.url().includes("/orders/sync"));
    await page.getByRole("button", { name: "Sync now" }).click();

    expect((await response).status()).toBeGreaterThanOrEqual(400);
    await expect(page.getByTestId("sync-error")).toBeVisible();
  });

  test("filters drive the request the server receives", async ({ page }) => {
    /**
     * The one place this suite stubs the platform's own list endpoint: a new
     * tenant has no orders, so the filter bar never renders against live data
     * (the empty state replaces it), and orders cannot be seeded by design.
     * What is under test is the UI's wire contract — that choosing a status
     * emits `status=`, and searching emits `q=` — not the server, which the
     * backend integration suite covers with real rows.
     */
    const ORDER = {
      id: "11111111-1111-4111-8111-111111111111",
      source: "aliexpress",
      externalId: "8123456789",
      externalStatus: "WAIT_BUYER_ACCEPT_GOODS",
      fulfillmentStatus: "shipped",
      paymentStatus: "paid",
      buyerName: "E2E Buyer",
      countryCode: "ES",
      currency: "USD",
      totalAmount: "23.9900",
      itemCount: 2,
      externalCreatedAt: "2026-07-20T10:00:00Z",
      lastSyncedAt: "2026-07-30T10:00:00Z",
      lastSyncError: null,
      createdAt: "2026-07-20T10:05:00Z",
    };
    const PAGE_BODY = {
      items: [ORDER],
      meta: {
        page: 1,
        size: 25,
        totalItems: 1,
        totalPages: 1,
        hasNext: false,
        hasPrevious: false,
      },
    };
    const STATS_BODY = {
      totalOrders: 1,
      byStatus: { shipped: 1 },
      pendingFulfillment: 0,
      processing: 0,
      delivered: 0,
      failedSyncsLast7Days: 0,
      lastSync: null,
      webhookEventsReceived: 0,
    };

    const listRequests: string[] = [];
    await page.route("**/api/v1/orders/statistics", (route) =>
      route.fulfill({ json: STATS_BODY }),
    );
    await page.route("**/api/v1/orders?**", (route) => {
      listRequests.push(route.request().url());
      return route.fulfill({ json: PAGE_BODY });
    });
    await page.route("**/api/v1/orders", (route) => {
      // Bare list URL without a query string (first paint).
      listRequests.push(route.request().url());
      return route.fulfill({ json: PAGE_BODY });
    });

    await registerAndSignIn(page);
    await page.goto("/orders");

    await expect(page.getByTestId("order-filters")).toBeVisible();
    await expect(page.getByTestId("order-row")).toHaveCount(1);
    await expect(page.getByText("8123456789")).toBeVisible();

    await page.getByLabel("Filter by status").selectOption("delivered");
    await expect
      .poll(() => listRequests.some((url) => url.includes("status=delivered")))
      .toBe(true);

    await page.getByLabel("Search orders").fill("buyer");
    await page.getByLabel("Search orders").press("Enter");
    await expect
      .poll(() => listRequests.some((url) => url.includes("q=buyer")))
      .toBe(true);
  });

  test("the detail page reports a missing order as an error, not a crash", async ({
    page,
  }) => {
    await registerAndSignIn(page);

    // A well-formed UUID that belongs to no tenant: the API answers 404 and
    // the page must show its error state rather than a blank screen.
    await page.goto("/orders/00000000-0000-4000-8000-000000000000");

    await expect(page.getByText("Could not load the order")).toBeVisible();
    // Next.js's route announcer is also role="alert", so target the error
    // state by its retry affordance rather than the bare role.
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });
});

test.describe("Orders route protection", () => {
  test("redirects an unauthenticated visitor to sign in", async ({ page }) => {
    await page.goto("/orders");
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("Orders responsiveness", () => {
  test.use({ viewport: { width: 320, height: 720 } });

  test("renders without horizontal overflow at 320px", async ({ page }) => {
    await registerAndSignIn(page);
    await page.goto("/orders");

    await expect(
      page.getByRole("heading", { name: "Orders", level: 1 }),
    ).toBeVisible();

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(overflows).toBe(false);
  });
});
