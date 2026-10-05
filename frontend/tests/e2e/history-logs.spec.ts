import type { Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * The three history lists (price changes, inventory sync runs and stock
 * changes, automation runs), backend-less. Each API list existed since
 * Phase 6 without a screen.
 */

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

function page1<T>(items: T[]) {
  return {
    items,
    meta: {
      page: 1,
      size: 20,
      totalItems: items.length,
      totalPages: 1,
      hasNext: false,
      hasPrevious: false,
    },
  };
}

const PRODUCT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const RULE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

test("the pricing page lists the prices the rules applied", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  await page.route("**/api/v1/pricing/rules**", (route) => json(route, page1([])));
  await page.route("**/api/v1/pricing/changes**", (route) =>
    json(
      route,
      page1([
        {
          id: "p1",
          productId: PRODUCT,
          previousPrice: "10.00",
          newPrice: "13.00",
          costPrice: "8.00",
          currency: "USD",
          reason: "pricing_engine_sync",
          appliedAt: "2026-10-05T08:00:00Z",
        },
      ]),
    ),
  );

  await page.goto("/pricing");
  const row = page.getByTestId("price-change-row");
  await expect(row).toContainText("10.00 → 13.00 USD");
  await expect(row).toContainText("pricing_engine_sync");
  await expect(row).toContainText(PRODUCT.slice(0, 8));
});

test("the inventory page shows sync runs and stock movements", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  await page.route("**/api/v1/inventory/sync-runs**", (route) =>
    json(
      route,
      page1([
        {
          id: "r1",
          storeId: null,
          productId: null,
          trigger: "scheduled",
          status: "failed",
          productsSeen: 12,
          productsChanged: 0,
          errorMessage: "supplier timeout",
          startedAt: "2026-10-05T02:00:00Z",
          finishedAt: "2026-10-05T02:01:00Z",
          createdAt: "2026-10-05T02:00:00Z",
        },
      ]),
    ),
  );
  await page.route("**/api/v1/inventory/changes**", (route) =>
    json(
      route,
      page1([
        {
          id: "c1",
          syncRunId: "r0",
          productId: PRODUCT,
          variantId: null,
          storeId: null,
          previousQuantity: 5,
          newQuantity: 0,
          reason: "supplier_sync",
          note: "sold out",
          createdAt: "2026-10-04T02:00:00Z",
        },
      ]),
    ),
  );
  await page.route("**/api/v1/inventory?**", (route) => json(route, page1([])));
  await page.route("**/api/v1/inventory", (route) => json(route, page1([])));

  await page.goto("/inventory");
  const run = page.getByTestId("inventory-run-row");
  await expect(run).toContainText("scheduled");
  await expect(run).toContainText("failed");
  await expect(run).toContainText("supplier timeout");
  await expect(run).toContainText("12 / 0");
  const change = page.getByTestId("inventory-change-row");
  await expect(change).toContainText("5 → 0");
  await expect(change).toContainText("supplier_sync · sold out");
});

test("the automation page names the rule each run belongs to", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  await page.route("**/api/v1/automation/rules**", (route) =>
    json(
      route,
      page1([
        {
          id: RULE,
          name: "Nightly inventory sync",
          action: "sync_inventory",
          schedule: "daily",
          storeId: null,
          config: {},
          isActive: true,
          lastRunAt: null,
          nextRunAt: null,
          consecutiveFailures: 0,
          createdAt: "2026-10-01T00:00:00Z",
          updatedAt: "2026-10-01T00:00:00Z",
        },
      ]),
    ),
  );
  await page.route("**/api/v1/automation/runs**", (route) =>
    json(
      route,
      page1([
        {
          id: "run1",
          ruleId: RULE,
          status: "succeeded",
          trigger: "scheduled",
          summary: "synced 40 products",
          errorMessage: null,
          startedAt: "2026-10-05T01:00:00Z",
          finishedAt: "2026-10-05T01:02:00Z",
          createdAt: "2026-10-05T01:00:00Z",
        },
      ]),
    ),
  );

  await page.goto("/automation");
  const row = page.getByTestId("automation-run-row");
  await expect(row).toContainText("Nightly inventory sync");
  await expect(row).toContainText("succeeded");
  await expect(row).toContainText("synced 40 products");
});
