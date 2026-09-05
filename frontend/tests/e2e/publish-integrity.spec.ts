import { expect, test, type Page } from "@playwright/test";

import {
  buildSyntheticProduct,
  DEMO_STORE_ID,
  mockPublishReadiness,
  openMockedEditor,
} from "./helpers/editor-fixture";

async function openReview(page: Page) {
  await page.getByTestId("editor-tab-publishing").click();
  await expect(page.getByTestId("publishing-panel")).toBeVisible();
}

async function selectDemoStore(page: Page) {
  await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
}

test.describe("UX-L2B save-before-publish integrity", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  function visiblePublishAction(page: Page) {
    return page.locator(`[data-testid="publish-action"]:visible`).first();
  }

  test("dirty draft + save success may continue to publish", async ({ page }) => {
    const order: string[] = [];
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      order.push("publish");
      const body = route.request().postDataJSON() as { expectedUpdatedAt?: string };
      expect(body.expectedUpdatedAt).toBeTruthy();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Published.",
          listingId: "66666666-6666-4666-8666-666666666666",
          externalProductId: "1001",
          externalHandle: "lamp",
          externalGraphqlId: null,
          shopDomain: "demo.myshopify.com",
          storefrontUrl: null,
          adminUrl: "https://demo.myshopify.com/admin/products/1001",
          onlineStorePublished: false,
          updated: true,
        }),
      });
    });

    await page.route("**/api/v1/drafts/**", async (route, request) => {
      if (request.method() === "PATCH") {
        order.push("save");
      }
      return route.fallback();
    });

    await page.getByLabel("Title").fill("Wireless Desk Lamp with USB Charging Updated");
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-ok")).toContainText(/Published/i, {
      timeout: 15_000,
    });
    expect(order.filter((step) => step === "save").length).toBeGreaterThanOrEqual(1);
    expect(order.filter((step) => step === "publish")).toEqual(["publish"]);
    const saveIndex = order.indexOf("save");
    const publishIndex = order.lastIndexOf("publish");
    expect(saveIndex).toBeGreaterThanOrEqual(0);
    expect(publishIndex).toBeGreaterThan(saveIndex);
  });

  test("dirty draft + save 500 → zero publish calls", async ({ page }) => {
    let publishCalls = 0;
    await openMockedEditor(page, {
      patchStatus: 500,
      patchBody: {
        code: "internal_error",
        message: "Could not save.",
        details: [],
        requestId: "req-l2b",
      },
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      return route.fulfill({ status: 500, body: "{}" });
    });

    await page.getByLabel("Title").fill("Broken save title");
    await openReview(page);
    await selectDemoStore(page);
    // Unsaved path still enables publish; click must stop after failed save.
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-save-failure")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("publish-save-failure")).toContainText(
      /couldn’t save your changes/i,
    );
    expect(publishCalls).toBe(0);
    expect(page.url()).toContain("tab=publishing");
  });

  test("dirty draft + save network failure → zero publish calls", async ({ page }) => {
    let publishCalls = 0;
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route(`**/api/v1/drafts/**`, async (route) => {
      if (route.request().method() === "PATCH") {
        return route.abort("failed");
      }
      return route.fallback();
    });
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      return route.fulfill({ status: 200, body: "{}" });
    });

    await page.getByLabel("Title").fill("Network fail title");
    await openReview(page);
    await selectDemoStore(page);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-save-failure")).toBeVisible({
      timeout: 10_000,
    });
    expect(publishCalls).toBe(0);
  });

  test("dirty draft + save 409 → conflict UI and zero publish calls", async ({ page }) => {
    let publishCalls = 0;
    await openMockedEditor(page, { patchStatus: 409 });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      return route.fulfill({ status: 200, body: "{}" });
    });

    await page.getByLabel("Title").fill("Conflict title");
    await openReview(page);
    await selectDemoStore(page);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-save-failure")).toContainText(
      /changed somewhere else/i,
      { timeout: 10_000 },
    );
    await expect(page.getByText(/Reload latest version|Review my changes/i).first()).toBeVisible();
    expect(publishCalls).toBe(0);
  });

  test("server blockers disable final publish only; header Review stays enabled", async ({
    page,
  }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish-readiness", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          mockPublishReadiness({
            canPublish: false,
            blockers: [
              {
                code: "selling_currency_mismatch",
                message: "Recalculate pricing for this store on the Pricing tab.",
                field: "sellPrice",
                section: "pricing",
                action: "Go to Pricing",
              },
            ],
            recommendations: [
              {
                code: "title_thin",
                message: "Add a clearer product title.",
                field: "title",
                section: "overview",
                action: "Go to Overview",
              },
            ],
          }),
        ),
      });
    });

    await expect(visiblePublishAction(page)).toBeEnabled();
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-blockers")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("publish-to-store")).toBeDisabled();
    await expect(page.getByTestId("publish-disabled-reason")).toContainText(
      /Fix the issues above/i,
    );
    await expect(page.getByTestId("publish-advice")).toContainText(/Worth checking/i);
    await expect(visiblePublishAction(page)).toBeEnabled();
    await page.getByRole("button", { name: "Go to Pricing" }).click();
    await expect(page.getByTestId("editor-tab-pricing")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("recommendations alone do not disable publication", async ({ page }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish-readiness", async (route) => {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          mockPublishReadiness({
            canPublish: true,
            blockers: [],
            recommendations: [
              {
                code: "images_missing",
                message: "Add at least one product image.",
                field: "images",
                section: "media",
                action: "Go to Media",
              },
            ],
          }),
        ),
      });
    });

    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-advice")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("publish-to-store")).toBeEnabled();
    await expect(page.getByText(/100% Ready|Perfect|Guaranteed/i)).toHaveCount(0);
  });

  test("readiness unavailable does not show ready", async ({ page }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish-readiness", async (route) => {
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          code: "internal_error",
          message: "boom",
          details: [],
          requestId: "x",
        }),
      });
    });

    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-status-summary")).toContainText(
      /couldn’t check this product/i,
      { timeout: 10_000 },
    );
    await expect(page.getByTestId("publish-to-store")).toBeDisabled();
    await expect(page.getByText(/No blocking issues found|100% Ready/i)).toHaveCount(0);
  });

  test("double click produces one publish request", async ({ page }) => {
    let publishCalls = 0;
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 800));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          message: "Published.",
          listingId: "66666666-6666-4666-8666-666666666666",
          externalProductId: "1001",
          externalHandle: "lamp",
          externalGraphqlId: null,
          shopDomain: "demo.myshopify.com",
          storefrontUrl: null,
          adminUrl: null,
          onlineStorePublished: false,
          updated: true,
        }),
      });
    });

    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await page.getByTestId("publish-to-store").click({ force: true });
    await expect(page.getByTestId("publish-ok")).toBeVisible({ timeout: 15_000 });
    expect(publishCalls).toBe(1);
  });

  test("422 structured blocker response renders plain text, not raw JSON", async ({
    page,
  }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      return route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          code: "validation_error",
          message: "This Shopify store is not connected. Reconnect it in Settings before publishing.",
          details: [
            { field: null, message: "publish_blocked", type: "reason" },
            {
              field: null,
              message: "store_disconnected",
              type: "blocker_codes",
            },
          ],
          requestId: "req-l2b",
        }),
      });
    });

    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-error")).toContainText(/not connected/i);
    await expect(page.getByText(/"blocker_codes"|\{"code"/)).toHaveCount(0);
  });

  test("choose-store state and missing shipping never appear as free", async ({ page }) => {
    const product = buildSyntheticProduct({
      shippingCost: null,
      requiresShipping: true,
    });
    await openMockedEditor(page, { product });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await expect(page.getByTestId("publish-status-summary")).toContainText(/Choose a store/i);
    await expect(page.getByText(/free shipping/i)).toHaveCount(0);
  });
});
