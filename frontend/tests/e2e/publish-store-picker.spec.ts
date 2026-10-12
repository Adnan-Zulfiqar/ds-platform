import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import {
  DEMO_PRODUCT_ID,
  DEMO_STORE_ID,
  mockShopifyStoresResponse,
  openMockedEditor,
} from "./helpers/editor-fixture";

/**
 * Review & publish: which stores are offered and why one is missing.
 * Pins down: each store names its channel; a lone store is chosen for the
 * merchant; a connected eBay account with no saved listing setup (so no eBay
 * store yet) is explained with a link to finish it, instead of eBay simply
 * being absent.
 */

const WOO_STORE_ID = "58585858-5858-4858-8858-585858585858";

type StoresBody = ReturnType<typeof mockShopifyStoresResponse>;

async function serveStores(page: Page, edit: (body: StoresBody) => void): Promise<void> {
  const body = mockShopifyStoresResponse();
  edit(body);
  await page.route("**/api/v1/stores**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }),
  );
}

async function ebayConnected(page: Page): Promise<void> {
  await page.route("**/api/v1/integrations/ebay/status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        connected: true,
        connection: { ebayUsername: "seller-one" },
      }),
    }),
  );
}

test("a lone store is chosen and named with its channel", async ({ page }) => {
  await openMockedEditor(page);
  await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
  const select = page.getByTestId("publish-store-select");
  await expect(select).toHaveValue(DEMO_STORE_ID, { timeout: 30_000 });
  await expect(select.locator("option:checked")).toHaveText("Demo Shopify · Shopify");
  await expect(page.getByTestId("publish-ebay-setup-pending")).toHaveCount(0);
});

test("with two stores nothing is chosen for the merchant", async ({ page }) => {
  await openMockedEditor(page);
  await serveStores(page, (body) => {
    body.items.push({ ...body.items[0], id: WOO_STORE_ID, name: "Corner Shop", platform: "woocommerce" });
  });
  await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
  const select = page.getByTestId("publish-store-select");
  await expect(select.locator("option")).toHaveText(
    ["Select a store…", "Demo Shopify · Shopify", "Corner Shop · WooCommerce"],
    { timeout: 30_000 },
  );
  await expect(select).toHaveValue("");
});

test("connected eBay without listing setup is explained, with the way to finish it", async ({
  page,
}) => {
  await openMockedEditor(page);
  await ebayConnected(page);
  await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
  const notice = page.getByTestId("publish-ebay-setup-pending");
  await expect(notice).toBeVisible({ timeout: 30_000 });
  await expect(notice).toContainText("Your eBay account is connected");
  await expect(notice.getByRole("link", { name: "Finish eBay setup" })).toHaveAttribute(
    "href",
    "/settings/integrations#channel-ebay",
  );
});

test("once eBay has its store, the notice goes and eBay is offered", async ({ page }) => {
  await openMockedEditor(page);
  await ebayConnected(page);
  await serveStores(page, (body) => {
    body.items.push({
      ...body.items[0],
      id: "56565656-5656-4656-8656-565656565656",
      name: "eBay United Kingdom",
      platform: "ebay",
    });
  });
  await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
  const select = page.getByTestId("publish-store-select");
  await expect(select.locator("option").last()).toHaveText("eBay United Kingdom · eBay", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("publish-ebay-setup-pending")).toHaveCount(0);
});

test("with no store at all the tab says so and links to connect one", async ({ page }) => {
  await openMockedEditor(page);
  await serveStores(page, (body) => {
    body.items = [];
    body.meta.totalItems = 0;
  });
  await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
  const empty = page.getByTestId("publish-no-stores");
  await expect(empty).toBeVisible({ timeout: 30_000 });
  await expect(empty.getByRole("link", { name: "Connect a store" })).toHaveAttribute(
    "href",
    "/settings/integrations",
  );
});
