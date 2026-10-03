import { expect, test } from "./fixtures/provider-isolation";
import {
  DEMO_PRODUCT_ID,
  mockPublishReadiness,
  mockShopifyStoresResponse,
  openMockedEditor,
} from "./helpers/editor-fixture";

/**
 * Track E7 W2 — Review & publish with a WooCommerce store, backend-less.
 * Pins down: choosing a WooCommerce store routes readiness and publish to
 * the WooCommerce endpoints, and nothing goes to Shopify.
 */

const WOO_STORE_ID = "58585858-5858-4858-8858-585858585858";

test("a WooCommerce store uses the WooCommerce readiness and publish endpoints", async ({
  page,
}) => {
  await openMockedEditor(page);
  const calls: string[] = [];
  const stores = mockShopifyStoresResponse();
  stores.items.push({
    ...stores.items[0],
    id: WOO_STORE_ID,
    name: "Corner Shop",
    slug: "woo-shop-example-com",
    platform: "woocommerce",
    currency: "GBP",
    settings: {},
  });
  await page.route("**/api/v1/stores**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stores) }),
  );
  await page.route("**/api/v1/integrations/*/publish-readiness", async (route) => {
    calls.push(`readiness:${new URL(route.request().url()).pathname}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        mockPublishReadiness({ channel: "woocommerce", storeId: WOO_STORE_ID }),
      ),
    });
  });
  await page.route("**/api/v1/integrations/woocommerce/publish", async (route) => {
    calls.push("publish:woocommerce");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        message: "Published to WooCommerce product 101.",
        listingId: "77777777-7777-4777-8777-777777777777",
        externalProductId: "101",
        storefrontUrl: "https://shop.example.com/p/101",
        updated: false,
        contentSource: "product",
        contentVersionId: null,
      }),
    });
  });

  await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
  const select = page.getByTestId("publish-store-select");
  await expect(select).toBeVisible({ timeout: 30_000 });
  await select.selectOption(WOO_STORE_ID);
  await expect
    .poll(() => calls.some((c) => c.endsWith("/integrations/woocommerce/publish-readiness")))
    .toBe(true);

  await page.getByRole("button", { name: "Publish to Store" }).first().click();

  await expect.poll(() => calls.includes("publish:woocommerce")).toBe(true);
  await expect(page.getByText("Published to WooCommerce product 101.").first()).toBeVisible();
  expect(calls.some((c) => c.includes("/integrations/shopify/"))).toBe(false);
});
