import type { Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";

/**
 * Track E7 W1 — the WooCommerce card, backend-less. Pins down: the connect
 * form sends exactly the four fields, a connected store is listed, and a
 * member sees the card read-only with no form.
 */

const STORE = {
  id: "55555555-5555-4555-8555-555555555555",
  name: "Corner Shop",
  slug: "woo-shop-example-com",
  platform: "woocommerce",
  status: "connected",
  storefrontUrl: "https://shop.example.com",
  externalStoreId: "shop.example.com",
  currency: "GBP",
  currencyLastSyncedAt: new Date().toISOString(),
  timezone: "UTC",
  settings: {},
  inventorySyncEnabled: true,
  pricingSyncEnabled: true,
  orderSyncEnabled: true,
  lastSyncAt: null,
  lastActivityAt: null,
  lastError: null,
  healthScore: 100,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

test("an owner connects a WooCommerce store and sees it listed", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  const sent: unknown[] = [];
  let stores: unknown[] = [];
  await page.route("**/api/v1/integrations/woocommerce/stores", (route) => json(route, stores));
  await page.route("**/api/v1/integrations/woocommerce/connect", (route) => {
    sent.push(route.request().postDataJSON());
    stores = [STORE];
    return json(route, STORE, 201);
  });

  await page.goto("/settings/integrations");
  const card = page.getByTestId("channel-woocommerce");
  await expect(card.getByTestId("channel-woocommerce-status")).toHaveText("Not connected");
  await card.getByRole("button", { name: "Connect a WooCommerce store" }).click();
  await card.getByLabel("Store name").fill("Corner Shop");
  await card.getByLabel("Store address").fill("https://shop.example.com");
  await card.getByLabel("Consumer key").fill("ck_" + "a".repeat(40));
  await card.getByLabel("Consumer secret").fill("cs_" + "b".repeat(40));
  await card.getByRole("button", { name: "Connect", exact: true }).click();

  await expect(card.getByTestId("woocommerce-stores")).toContainText("Corner Shop");
  await expect(card.getByTestId("channel-woocommerce-status")).toHaveText("Connected");
  expect(sent).toEqual([
    {
      name: "Corner Shop",
      siteUrl: "https://shop.example.com",
      consumerKey: "ck_" + "a".repeat(40),
      consumerSecret: "cs_" + "b".repeat(40),
    },
  ]);
});

test("a member sees the WooCommerce card read-only", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld({ role: "member" }));
  await page.route("**/api/v1/integrations/woocommerce/stores", (route) => json(route, [STORE]));
  await page.goto("/settings/integrations");
  const card = page.getByTestId("channel-woocommerce");
  await expect(card.getByTestId("woocommerce-stores")).toContainText("Corner Shop");
  await expect(card.getByTestId("channel-woocommerce-read-only")).toBeVisible();
  await expect(card.getByRole("button", { name: /Connect|Disconnect/ })).toHaveCount(0);
});
