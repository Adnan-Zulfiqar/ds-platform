import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

import {
  buildSyntheticProduct,
  DEMO_PRODUCT_ID,
  openMockedEditor,
  UX_L2A_SHOT_ROOT,
} from "./helpers/editor-fixture";
import type { StoreListing } from "@/types/api";

function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

async function assertNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  expect(overflow).toBe(false);
}

async function shot(
  page: Page,
  relativeName: string,
  options?: { fullPage?: boolean },
) {
  const destinations = [
    path.join(UX_L2A_SHOT_ROOT, "after", relativeName),
    path.join("test-results", "ux-l2a-after", relativeName),
  ];
  const buffer = await page.screenshot({
    fullPage: options?.fullPage ?? false,
  });
  for (const filePath of destinations) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, buffer);
  }
}

test.describe("UX-L2A editor foundation — desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light", locale: "en-GB" });

  test("command bar, grouped nav, checklist and save wording", async ({
    page,
  }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await expect(page.getByTestId("product-editor-header")).toBeVisible();
    await expect(page.getByRole("link", { name: /Back to drafts/i })).toBeVisible();
    await expect(page.getByTestId("product-editor-title")).toBeVisible();
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Draft");
    await expect(page.getByTestId("draft-save-state")).toContainText(
      /Draft saved — not live|Unsaved changes/,
    );
    await expect(page.getByTestId("product-editor-store")).toContainText(
      /Connect Shopify to publish/,
    );

    await expect(page.getByTestId("editor-nav-group-product")).toBeVisible();
    await expect(page.getByTestId("editor-nav-group-selling")).toBeVisible();
    await expect(page.getByTestId("editor-nav-group-improve")).toBeVisible();
    await expect(page.getByTestId("editor-nav-group-publish")).toBeVisible();
    await expect(page.getByTestId("editor-tab-overview")).toBeVisible();
    await expect(page.getByTestId("editor-tab-history")).toHaveCount(0);

    await expect(page.getByTestId("publish-checklist-aside")).toContainText(
      "Before you publish",
    );
    await expect(page.getByTestId("publish-checklist-aside")).toContainText(
      /Required checks look complete|Fix \d+ thing/,
    );
    // Recommended only when SEO gaps exist — this product has SEO filled.
    await expect(page.locator("text=raw backend").or(page.locator("text={code}"))).toHaveCount(0);

    await assertNoHorizontalOverflow(page);
    await shot(page, "1440x900-normal-light.png");

    await page.getByRole("button", { name: "Preview" }).first().click();
    await expect(page.getByTestId("draft-preview-panel")).toBeVisible();
    await page.keyboard.press("Escape");

    await visibleTestId(page, "product-actions-menu").click();
    await expect(
      page.getByRole("menuitem", { name: /Refresh supplier information/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("menuitem", { name: /View history/i }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
  });

  test("long title stays two lines and does not hide actions", async ({
    page,
  }) => {
    const longTitle =
      "Ultra Premium Ergonomic Wireless Desk Lamp With USB-C Charging Hub Ambient Modes And Extra Long Marketing Subtitle For Overflow Checks";
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ title: longTitle }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-editor-title")).toContainText("Ultra Premium");
    await expect(visibleTestId(page, "publish-action")).toHaveCount(1);
    await assertNoHorizontalOverflow(page);
    await shot(page, "1440x900-long-title.png");
  });

  test("broken image shows professional fallback", async ({ page }) => {
    await page.route("https://broken.example/**", (route) =>
      route.fulfill({ status: 404, body: "missing" }),
    );
    await openMockedEditor(page, {
      product: buildSyntheticProduct({
        images: [
          {
            id: "55555555-5555-4555-8555-555555555555",
            url: "https://broken.example/missing.png",
            position: 0,
            altText: null,
            isSupplier: true,
          },
        ],
      }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-editor-thumbnail-error")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Product image unavailable").first()).toBeAttached();
    await shot(page, "1440x900-broken-image.png");
  });

  test("shipping unavailable and stale supplier copy", async ({ page }) => {
    const stale = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString();
    await openMockedEditor(page, {
      product: buildSyntheticProduct({
        shippingCost: null,
        lastSyncedAt: stale,
        importShipToCheckedAt: stale,
        lastSyncError: null,
      }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Supplier information may be out of date")).toBeVisible();

    await page.getByTestId("editor-tab-shipping").click();
    await expect(page.getByTestId("shipping-unavailable")).toBeVisible();
    await expect(page.getByText("Shipping price not available")).toBeVisible();
    await expect(
      page.getByText(/will not treat missing shipping as free/i),
    ).toBeVisible();
    await expect(page.getByTestId("shipping-refresh")).toContainText(
      "Check shipping again",
    );
    await expect(page.getByText("United Kingdom").first()).toBeVisible();
    await expect(page.getByText("$0.00")).toHaveCount(0);
    await expect(page.getByText(/free shipping/i)).toHaveCount(0);
    await shot(page, "1440x900-shipping-unavailable.png", { fullPage: true });
  });

  test("store disconnected wording and blockers checklist", async ({ page }) => {
    await openMockedEditor(page, {
      product: buildSyntheticProduct({
        title: "Ab",
        description: null,
        images: [],
        variants: [],
        costPriceMin: null,
        seoTitle: null,
        slug: null,
      }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-editor-store")).toContainText(
      "Connect Shopify to publish",
    );
    await expect(page.getByTestId("publish-checklist-aside")).toContainText("Required");
    await expect(page.getByTestId("publish-checklist-aside")).toContainText("Recommended");
    await expect(page.getByText("Before you publish").first()).toBeVisible();
    await shot(page, "1440x900-publish-blockers.png");
  });

  test("keyboard tab navigation across grouped sections", async ({ page }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    const overview = page.getByTestId("editor-tab-overview");
    await overview.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("editor-tab-description")).toBeFocused();
    await page.keyboard.press("Home");
    await expect(page.getByTestId("editor-tab-overview")).toBeFocused();
  });
});

test.describe("UX-L2A editor foundation — dark mode", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });

  test("dark command bar without overflow", async ({ page }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await assertNoHorizontalOverflow(page);
    await shot(page, "1440x900-dark.png");
  });
});

test.describe("UX-L2A editor foundation — viewports", () => {
  for (const viewport of [
    { width: 1920, height: 1080, name: "1920x1080" },
    { width: 1280, height: 800, name: "1280x800" },
    { width: 1024, height: 768, name: "1024x768" },
  ] as const) {
    test(`layout at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await openMockedEditor(page);
      await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
      await assertNoHorizontalOverflow(page);
      await shot(page, `${viewport.name}-normal.png`);
    });
  }
});

test.describe("UX-L2A editor foundation — mobile", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("mobile bar, things to fix sheet, no overflow", async ({ page }) => {
    await openMockedEditor(page, {
      product: buildSyntheticProduct({
        title: "Ab",
        description: null,
        images: [],
        seoTitle: null,
        slug: null,
      }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("mobile-editor-action-bar")).toBeVisible();
    await expect(visibleTestId(page, "product-editor-actions")).toHaveCount(0);

    await page.getByRole("button", { name: /Things to fix/i }).click();
    await expect(page.getByTestId("publish-checklist-sheet")).toBeVisible();
    await expect(
      page
        .getByTestId("publish-checklist-sheet")
        .getByRole("heading", { name: "Before you publish" }),
    ).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await shot(page, "390x844-mobile-sheet.png");
    await page
      .getByRole("dialog", { name: "Before you publish" })
      .getByRole("button", { name: "Close checklist" })
      .click();
    await expect(page.getByTestId("publish-checklist-sheet")).toHaveCount(0);
  });
});

test.describe("UX-L2A editor foundation — save failure", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("save failure shows try again without raw JSON", async ({ page }) => {
    await openMockedEditor(page, {
      patchStatus: 500,
      patchBody: {
        code: "internal_error",
        message: "Could not save.",
        details: [{ field: null, message: "db", type: null }],
        requestId: "req-ux-l2a-fail",
      },
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.locator("#draft-title").fill(
      "Updated title that should trigger autosave eventually",
    );
    // Force manual save if the button appears when dirty.
    const save = visibleTestId(page, "save-draft");
    await expect(save).toBeVisible({ timeout: 10_000 });
    await save.click();
    await expect(page.getByTestId("draft-save-state")).toContainText(/Couldn.?t save/i, {
      timeout: 15_000,
    });
    await expect(page.getByTestId("draft-save-retry")).toBeVisible();
    await expect(page.getByText(/"code":\s*"internal_error"/)).toHaveCount(0);
    await shot(page, "1440x900-save-failure.png");
  });
});

test.describe("UX-L2A editor foundation — live listing copy", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("live store save wording when listing is synced", async ({ page }) => {
    const listing: StoreListing = {
      id: "66666666-6666-4666-8666-666666666666",
      storeId: "77777777-7777-4777-8777-777777777777",
      productId: DEMO_PRODUCT_ID,
      externalProductId: "gid://shopify/Product/1",
      externalHandle: "wireless-desk-lamp",
      externalGraphqlId: "gid://shopify/Product/1",
      shopDomain: "demo-shop.myshopify.com",
      storefrontUrl: "https://demo-shop.myshopify.com/products/wireless-desk-lamp",
      adminUrl: "https://demo-shop.myshopify.com/admin/products/1",
      onlineStorePublished: true,
      status: "synced",
      lastSyncedAt: new Date().toISOString(),
      lastError: null,
      publishedAt: new Date().toISOString(),
      lastFailedSyncAt: null,
    };
    await openMockedEditor(page, { listings: [listing] });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Published");
    await expect(page.getByTestId("draft-save-state")).toContainText(
      /Changes saved as a draft — your live product has not changed/,
    );
    await expect(page.getByTestId("product-editor-store")).toContainText(
      "demo-shop.myshopify.com",
    );
    await shot(page, "1440x900-live-draft-wording.png");
  });
});
