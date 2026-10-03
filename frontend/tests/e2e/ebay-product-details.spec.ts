import type { Page, Route } from "@playwright/test";

import type { EbayProductDetails, EbayProductDetailsPayload } from "@/types/api";

import { expect, test } from "./fixtures/provider-isolation";
import { ebayConnection } from "./helpers/channels-fixture";
import {
  DEMO_PRODUCT_ID,
  mockPublishReadiness,
  mockShopifyStoresResponse,
  openMockedEditor,
} from "./helpers/editor-fixture";

/**
 * EBAY-C3 "eBay details" in Review & publish — backend-less.
 *
 * Layered over the mocked editor: eBay status says connected, and the three
 * C3 product-detail endpoints are answered from a small mutable world. Pins
 * down: hidden without a connection; suggestions from eBay are pickable;
 * saving sends exactly the category and aspects; required aspects missing
 * are named.
 */

const MUG_ASPECTS: EbayProductDetails["categoryAspects"] = [
  { name: "Brand", required: true, selectionOnly: false, multiple: false, values: ["Acme", "Unbranded"] },
  { name: "Colour", required: false, selectionOnly: false, multiple: true, values: [] },
];

function emptyDetails(): EbayProductDetails {
  return {
    marketplaceId: "EBAY_GB",
    categoryId: null,
    categoryName: null,
    aspects: {},
    categoryAspects: [],
    missingRequired: [],
  };
}

async function mockEbayDetails(page: Page, connected = true) {
  const world = { details: emptyDetails(), saves: [] as EbayProductDetailsPayload[] };
  await page.route("**/api/v1/integrations/ebay/status", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        connected
          ? { configured: true, connected: true, connection: ebayConnection({ marketplaceId: "EBAY_GB" }) }
          : { configured: true, connected: false, connection: null },
      ),
    }),
  );
  await page.route("**/api/v1/integrations/ebay/products/*/category-suggestions*", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { categoryId: "20625", name: "Mugs", path: "Home & Garden > Kitchen > Mugs" },
        { categoryId: "177018", name: "Cups", path: "Home & Garden > Kitchen > Cups" },
      ]),
    }),
  );
  await page.route("**/api/v1/integrations/ebay/products/*/details*", async (route: Route) => {
    if (route.request().method() === "PUT") {
      const payload = route.request().postDataJSON() as EbayProductDetailsPayload;
      world.saves.push(payload);
      const missing = payload.aspects.Brand?.length ? [] : ["Brand"];
      world.details = {
        marketplaceId: payload.marketplaceId,
        categoryId: payload.categoryId,
        categoryName: payload.categoryName ?? null,
        aspects: payload.aspects,
        categoryAspects: MUG_ASPECTS,
        missingRequired: missing,
      };
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(world.details) });
  });
  return world;
}

async function openPublishing(page: Page, connected = true) {
  await openMockedEditor(page);
  const world = await mockEbayDetails(page, connected);
  await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
  return world;
}

test.describe("eBay details in Review & publish (EBAY-C3)", () => {
  test("hidden when eBay is not connected", async ({ page }) => {
    await openPublishing(page, false);
    await expect(page.getByTestId("publish-action").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("ebay-product-details")).toHaveCount(0);
  });

  test("pick a suggested category, fill the required brand, save exactly that", async ({ page }) => {
    const world = await openPublishing(page);
    const section = page.getByTestId("ebay-product-details");
    await expect(section).toBeVisible({ timeout: 30_000 });
    await expect(section.getByTestId("ebay-current-category")).toContainText("No category chosen yet");

    await section.getByRole("button", { name: "Suggest categories" }).click();
    await section.getByRole("button", { name: "Home & Garden > Kitchen > Mugs" }).click();
    await section.getByRole("button", { name: "Save eBay details" }).click();

    // The new category's aspects arrive with the save; Brand is still missing.
    await expect(section.getByTestId("ebay-missing-required")).toContainText("Brand");
    expect(world.saves[0]).toMatchObject({ marketplaceId: "EBAY_GB", categoryId: "20625", aspects: {} });

    await section.getByLabel("Brand (required)").fill("Acme");
    await section.getByLabel("Colour").fill("Red, Blue");
    await section.getByRole("button", { name: "Save eBay details" }).click();

    await expect(section.getByTestId("ebay-missing-required")).toHaveCount(0);
    expect(world.saves[1].aspects).toEqual({ Brand: ["Acme"], Colour: ["Red", "Blue"] });
  });
});

const EBAY_STORE_ID = "66666666-6666-4666-8666-666666666666";

test.describe("Publish to eBay from Review & publish (EBAY-C3)", () => {
  test("an eBay store uses the eBay readiness and publish endpoints", async ({ page }) => {
    await openMockedEditor(page);
    await mockEbayDetails(page);
    const calls: string[] = [];
    const stores = mockShopifyStoresResponse();
    stores.items.push({
      ...stores.items[0],
      id: EBAY_STORE_ID,
      name: "eBay United Kingdom",
      slug: "ebay-marketplace-gb",
      platform: "ebay",
      currency: "GBP",
      settings: { countryCode: "GB", ebayMarketplaceId: "EBAY_GB" },
    });
    await page.route("**/api/v1/stores**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(stores) }),
    );
    await page.route("**/api/v1/integrations/*/publish-readiness", async (route) => {
      calls.push(`readiness:${new URL(route.request().url()).pathname}`);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockPublishReadiness({ channel: "ebay", storeId: EBAY_STORE_ID })),
      });
    });
    await page.route("**/api/v1/integrations/ebay/publish", async (route) => {
      calls.push("publish:ebay");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Published to eBay listing 110000000001.",
          listingId: "77777777-7777-4777-8777-777777777777",
          externalProductId: "110000000001",
          storefrontUrl: "https://www.ebay.co.uk/itm/110000000001",
          updated: false,
          contentSource: "product",
          contentVersionId: null,
        }),
      });
    });

    await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
    const select = page.getByTestId("publish-store-select");
    await expect(select).toBeVisible({ timeout: 30_000 });
    await select.selectOption(EBAY_STORE_ID);
    await expect.poll(() => calls.some((c) => c.endsWith("/integrations/ebay/publish-readiness"))).toBe(true);

    await page.getByRole("button", { name: "Publish to Store" }).first().click();

    await expect.poll(() => calls.includes("publish:ebay")).toBe(true);
    await expect(page.getByText("Published to eBay listing 110000000001.").first()).toBeVisible();
    expect(calls.some((c) => c.includes("/integrations/shopify/"))).toBe(false);
  });
});
