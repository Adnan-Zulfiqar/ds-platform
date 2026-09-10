/**
 * UX-L2B-R4 — capture store guidance chrome for connected / disconnected states.
 * Screenshots land under test-results/ux-l2b-r4-store-guidance/ by default.
 */
import { expect, test } from "@playwright/test";

import { captureEvidenceScreenshot } from "./helpers/screenshot-evidence";
import {
  DEMO_STORE_ID,
  mockShopifyStoresResponse,
  openMockedEditor,
} from "./helpers/editor-fixture";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";

const SHOT_ROOT = resolveSuiteShotRoot("ux-l2b-r4-store-guidance", [
  "UX_L2B_R4_SHOT_ROOT",
]);

async function shot(page: import("@playwright/test").Page, name: string) {
  await captureEvidenceScreenshot(page, name, { root: SHOT_ROOT, fullPage: false });
}

test.describe("UX-L2B-R4 store guidance screenshots", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("connected store without listing", async ({ page }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("product-editor-store")).toContainText(
      /Choose a store in Review & publish/,
    );
    await shot(page, "header-connected-choose-store-light");
    await page.emulateMedia({ colorScheme: "dark" });
    await shot(page, "header-connected-choose-store-dark");
  });

  test("no usable Shopify store", async ({ page }) => {
    await openMockedEditor(page);
    await page.unroute("**/api/v1/stores**");
    await page.route("**/api/v1/stores**", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const body = mockShopifyStoresResponse();
      body.items[0] = {
        ...body.items[0],
        id: DEMO_STORE_ID,
        status: "disconnected",
      };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });
    await page.reload();
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-editor-store")).toContainText(
      /Connect Shopify to publish/,
    );
    await shot(page, "header-disconnected-connect-shopify-light");
  });
});
