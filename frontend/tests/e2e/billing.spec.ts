import type { Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * Track E6c — Settings → Billing, backend-less. Pins down: the trial and
 * usage render, choosing a plan sends the plan and add-on to Checkout and
 * follows the Stripe URL, a return from Checkout re-reads the subscription
 * once, and a viewer sees no actions.
 */

const PLANS = [
  { key: "starter", name: "Starter", priceUsd: 12, listingLimit: 200, aiAddonUsd: 8 },
  { key: "growth", name: "Growth", priceUsd: 30, listingLimit: 450, aiAddonUsd: 12 },
  { key: "pro", name: "Pro", priceUsd: 70, listingLimit: 1000, aiAddonUsd: 17 },
];

const TRIAL = {
  configured: true,
  plan: null,
  status: "none",
  aiAddon: false,
  trialEndsAt: "2026-11-03T00:00:00Z",
  onTrial: true,
  paid: false,
  listingLimit: 450,
  listingsUsed: 37,
  canWrite: true,
  canUseAi: false,
  cancelAtPeriodEnd: false,
  currentPeriodEnd: null,
  hasCustomer: false,
  plans: PLANS,
};

const PAID = {
  ...TRIAL,
  plan: "growth",
  status: "active",
  aiAddon: true,
  onTrial: false,
  paid: true,
  canUseAi: true,
  currentPeriodEnd: "2026-11-04T00:00:00Z",
  hasCustomer: true,
};

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

test("a trial owner picks Pro with AI and is sent to Stripe Checkout", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  const sent: unknown[] = [];
  await page.route("**/api/v1/billing", (route) => json(route, TRIAL));
  await page.route("**/api/v1/billing/checkout", (route) => {
    sent.push(route.request().postDataJSON());
    return json(route, { url: "https://checkout.stripe.test/c/pay/cs_test_1" });
  });
  await page.route("https://checkout.stripe.test/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<p>stripe</p>" }),
  );

  await page.goto("/settings/billing");
  await expect(page.getByTestId("billing-state")).toHaveText("Free trial");
  await expect(page.getByTestId("billing-usage")).toHaveText("37 / 450");

  await page.getByLabel(/Add unlimited AI/).check();
  await expect(page.getByTestId("billing-plans")).toContainText("$87");
  await page.getByRole("button", { name: "Choose Pro" }).click();

  await page.waitForURL("https://checkout.stripe.test/**");
  expect(sent).toEqual([{ plan: "pro", aiAddon: true }]);
});

test("returning from Checkout re-reads the subscription once", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  let syncs = 0;
  await page.route("**/api/v1/billing", (route) => json(route, TRIAL));
  await page.route("**/api/v1/billing/sync", (route) => {
    syncs += 1;
    return json(route, PAID);
  });

  await page.goto("/settings/billing?checkout=success");
  await expect(page.getByTestId("billing-state")).toHaveText("Growth plan · active");
  await expect(page.getByRole("button", { name: "Current plan" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Payment method and invoices" })).toBeVisible();
  expect(syncs).toBe(1);
});

test("a viewer sees the plan but no billing actions", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld({ role: "viewer" }));
  await page.route("**/api/v1/billing", (route) => json(route, PAID));

  await page.goto("/settings/billing");
  await expect(page.getByTestId("billing-state")).toHaveText("Growth plan · active");
  await expect(page.getByText("Only the workspace owner can change the plan.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Switch to|Payment method/ })).toHaveCount(0);
});

test("the settings index links to billing", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  await page.goto("/settings");
  await expect(page.getByRole("link", { name: /Billing/ })).toHaveAttribute(
    "href",
    "/settings/billing",
  );
});
