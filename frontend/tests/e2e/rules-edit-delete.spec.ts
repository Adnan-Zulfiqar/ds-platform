import type { Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * Automation and pricing rules can be edited, paused and deleted, backend-
 * less. The API had PATCH and DELETE for both since Phase 6; the UI only
 * offered create. Pins down the exact bodies sent and that delete needs a
 * second click.
 */

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

const AUTOMATION_RULE = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
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
};

const PRICING_RULE = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  name: "Default markup",
  scope: "global",
  strategy: "percentage_markup",
  storeId: null,
  categoryId: null,
  productId: null,
  priority: 100,
  markupPercent: "30",
  markupFixed: null,
  minProfit: null,
  maxPrice: null,
  tiers: [],
  currency: "USD",
  isActive: true,
  createdAt: "2026-10-01T00:00:00Z",
  updatedAt: "2026-10-01T00:00:00Z",
};

/** The real `Page` envelope: `items` plus `meta`. */
function page1(items: unknown[]) {
  return {
    items,
    meta: {
      page: 1,
      size: 50,
      totalItems: items.length,
      totalPages: 1,
      hasNext: false,
      hasPrevious: false,
    },
  };
}

/** One in-memory list per test, mutated by the PATCH and DELETE handlers. */
async function mockRules(
  page: import("@playwright/test").Page,
  base: string,
  initial: Record<string, unknown>,
) {
  const rules = [{ ...initial }];
  const calls: Array<{ method: string; url: string; body: unknown }> = [];
  await page.route(`**/api/v1/${base}/rules**`, (route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    if (method === "GET") return json(route, page1(rules));
    const id = url.pathname.split("/").pop() ?? "";
    calls.push({ method, url: url.pathname, body: request.postDataJSON() });
    if (method === "PATCH") {
      const rule = rules.find((r) => r.id === id);
      Object.assign(rule ?? {}, request.postDataJSON() as object);
      return json(route, rule);
    }
    if (method === "DELETE") {
      rules.splice(0, rules.length, ...rules.filter((r) => r.id !== id));
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });
  return calls;
}

test("an automation rule is renamed, rescheduled, paused and deleted", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  const calls = await mockRules(page, "automation", AUTOMATION_RULE);

  await page.goto("/automation");
  const row = page.getByTestId("automation-rule-row");
  await expect(row).toContainText("Nightly inventory sync");

  await row.getByRole("button", { name: "Edit Nightly inventory sync" }).click();
  await row.getByLabel("Name of Nightly inventory sync").fill("Hourly inventory sync");
  await row.getByLabel("Schedule of Nightly inventory sync").selectOption("hourly");
  await row.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("status")).toHaveText("Rule updated.");
  await expect(row).toContainText("Hourly inventory sync");
  await expect(row).toContainText("hourly");

  await row.getByRole("button", { name: "Pause Hourly inventory sync" }).click();
  await expect(row).toContainText("inactive");

  // One click arms, the second deletes.
  await row.getByRole("button", { name: "Delete Hourly inventory sync" }).click();
  await expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  await row.getByRole("button", { name: "Confirm delete Hourly inventory sync" }).click();
  await expect(page.getByText("No automation rules")).toBeVisible();

  expect(calls).toEqual([
    {
      method: "PATCH",
      url: `/api/v1/automation/rules/${AUTOMATION_RULE.id}`,
      body: { name: "Hourly inventory sync", schedule: "hourly" },
    },
    {
      method: "PATCH",
      url: `/api/v1/automation/rules/${AUTOMATION_RULE.id}`,
      body: { isActive: false },
    },
    { method: "DELETE", url: `/api/v1/automation/rules/${AUTOMATION_RULE.id}`, body: null },
  ]);
});

test("a pricing rule's markup and guards are edited, and an empty guard clears it", async ({
  page,
}) => {
  await mockChannelsApi(page, channelsWorld());
  const calls = await mockRules(page, "pricing", { ...PRICING_RULE, minProfit: "2.00" });

  await page.goto("/pricing");
  const row = page.getByTestId("pricing-rule-row");
  await expect(row).toContainText("30%");
  await expect(row).toContainText("min profit 2.00");

  await row.getByRole("button", { name: "Edit Default markup" }).click();
  await row.getByLabel("Markup percent of Default markup").fill("35");
  await row.getByLabel("Min profit of Default markup").fill("");
  await row.getByLabel("Max price of Default markup").fill("99.99");
  await row.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("status")).toHaveText("Rule updated.");
  await expect(row).toContainText("35%");
  await expect(row).toContainText("max 99.99");
  await expect(row).not.toContainText("min profit");

  expect(calls).toEqual([
    {
      method: "PATCH",
      url: `/api/v1/pricing/rules/${PRICING_RULE.id}`,
      body: { name: "Default markup", markupPercent: "35", minProfit: null, maxPrice: "99.99" },
    },
  ]);
});

test("a delete that is not confirmed within the window disarms itself", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  const calls = await mockRules(page, "pricing", PRICING_RULE);

  await page.goto("/pricing");
  const row = page.getByTestId("pricing-rule-row");
  await row.getByRole("button", { name: "Delete Default markup" }).click();
  await expect(row.getByRole("button", { name: "Confirm delete Default markup" })).toBeVisible();
  await expect(row.getByRole("button", { name: "Delete Default markup" })).toBeVisible({
    timeout: 6000,
  });
  expect(calls).toEqual([]);
});
