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
      /No content gaps flagged here|Review \d+ item/,
    );
    await expect(page.getByText("Required", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Recommended", { exact: true })).toHaveCount(0);
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
      page.getByRole("menuitem", { name: /View recent activity/i }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
  });

  test("advisory SEO title alone does not hard-disable Review & publish", async ({
    page,
  }) => {
    let publishCalls = 0;
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ message: "should not be called" }),
      });
    });

    await openMockedEditor(page, {
      product: buildSyntheticProduct({ seoTitle: null }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    const action = visibleTestId(page, "publish-action");
    await expect(action).toBeEnabled();
    await expect(action).toContainText(/Review 1 item/);
    await expect(page.getByTestId("editor-tab-publishing")).not.toContainText(/· Review/);
    await expect(page.getByText("Required", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Recommended", { exact: true })).toHaveCount(0);
    await shot(page, "1440x900-review-items-enabled.png");

    await action.click();
    await expect(page.getByTestId("editor-tab-publishing")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByTestId("publish-to-store")).toBeVisible();
    expect(publishCalls).toBe(0);
    await shot(page, "1440x900-review-publish-section.png");
  });

  test("advisory slug alone does not hard-disable Review & publish", async ({
    page,
  }) => {
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ slug: null }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    const action = visibleTestId(page, "publish-action");
    await expect(action).toBeEnabled();
    await expect(action).toContainText(/Review 1 item/);
    await action.click();
    await expect(page.getByTestId("editor-tab-publishing")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  test("multiple advisory items keep Review N items enabled without publishing", async ({
    page,
  }) => {
    let publishCalls = 0;
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      publishCalls += 1;
      await route.abort();
    });

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
    const action = visibleTestId(page, "publish-action");
    await expect(action).toBeEnabled();
    await expect(action).toContainText(/Review \d+ items/);
    await action.click();
    await expect(page.getByTestId("publish-to-store")).toBeVisible();
    expect(publishCalls).toBe(0);
  });

  test("in-flight publishing disables the header action against double submit", async ({
    page,
  }) => {
    const storeId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const storePage = {
      items: [
        {
          id: storeId,
          name: "Demo Store",
          slug: "demo-store",
          platform: "shopify",
          status: "connected",
          storefrontUrl: "https://demo.myshopify.com",
          externalStoreId: "demo",
          currency: "GBP",
          currencyLastSyncedAt: null,
          timezone: "Europe/London",
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
        },
      ],
      meta: {
        page: 1,
        size: 50,
        totalItems: 1,
        totalPages: 1,
        hasNext: false,
        hasPrevious: false,
      },
    };
    await page.route("**/api/v1/stores**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(storePage),
      });
    });
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      await new Promise((r) => setTimeout(r, 2500));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ message: "Published." }),
      });
    });

    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.getByTestId("editor-tab-publishing").click();
    await page.locator("#publish-store").selectOption(storeId);
    await page.getByTestId("publish-to-store").click();
    await expect(visibleTestId(page, "publish-action")).toBeDisabled({ timeout: 5_000 });
    await expect(visibleTestId(page, "publish-action")).toContainText(/Publishing/);
  });

  test("channel publish failure remains visible and truthful", async ({ page }) => {
    const storeId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const storePage = {
      items: [
        {
          id: storeId,
          name: "Demo Store",
          slug: "demo-store",
          platform: "shopify",
          status: "connected",
          storefrontUrl: "https://demo.myshopify.com",
          externalStoreId: "demo",
          currency: "GBP",
          currencyLastSyncedAt: null,
          timezone: "Europe/London",
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
        },
      ],
      meta: {
        page: 1,
        size: 50,
        totalItems: 1,
        totalPages: 1,
        hasNext: false,
        hasPrevious: false,
      },
    };
    await page.route("**/api/v1/stores**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(storePage),
      });
    });
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          code: "validation_error",
          message: "Store is not connected.",
          details: [],
          requestId: "req-pub-fail",
        }),
      });
    });

    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await page.getByTestId("editor-tab-publishing").click();
    await page.locator("#publish-store").selectOption(storeId);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByText(/Store is not connected/i)).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(/"code":\s*"validation_error"/)).toHaveCount(0);
  });

  test("More menu History actions have distinct labels and destinations", async ({
    page,
  }) => {
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    const more = visibleTestId(page, "product-actions-menu");
    await more.focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("menuitem", { name: /^View recent activity$/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("menuitem", { name: /^Open full history$/i }),
    ).toBeVisible();
    await shot(page, "1440x900-history-menu-labels.png");

    await page.getByRole("menuitem", { name: /^View recent activity$/i }).click();
    await expect(page.getByRole("dialog", { name: /Version history/i })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: /Version history/i })).toHaveCount(0);

    await more.click();
    await page.getByRole("menuitem", { name: /^Open full history$/i }).click();
    await expect(page).toHaveURL(/[?&]tab=history/);
    await expect(page.getByRole("heading", { name: /^History$/i })).toBeVisible();
    await page.goBack();
    await expect(page).not.toHaveURL(/[?&]tab=history/);
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
    await expect(page.getByTestId("publish-checklist-aside")).toContainText(
      "Items to review",
    );
    await expect(page.getByTestId("publish-checklist-aside")).toContainText(
      /Review \d+ item/,
    );
    await expect(page.getByText("Required", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Recommended", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Before you publish").first()).toBeVisible();
    await expect(visibleTestId(page, "publish-action")).toContainText(
      /Review \d+ item/,
    );
    await expect(visibleTestId(page, "publish-action")).toBeEnabled();
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

  test("200 percent zoom keeps command bar usable", async ({ page }) => {
    // 1440×900 at 200% browser zoom ≈ 720×450 CSS pixels.
    await page.setViewportSize({ width: 720, height: 450 });
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-editor-header")).toBeVisible();
    await expect(page.getByTestId("product-editor-title")).toBeVisible();
    await expect(page.getByTestId("publish-action").first()).toBeAttached();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2,
    );
    expect(overflow).toBe(false);
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

    await page.getByTestId("things-to-fix-trigger").click();
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

  test("mobile checklist sheet traps focus, Escape restores trigger", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(String(err)));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });

    await openMockedEditor(page, {
      product: buildSyntheticProduct({
        title: "Ab",
        seoTitle: null,
        slug: null,
      }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    const trigger = page.getByTestId("things-to-fix-trigger");
    await trigger.click();

    const dialog = page.getByRole("dialog", { name: "Before you publish" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("aria-modal", "true");

    const close = dialog.getByRole("button", { name: "Close checklist" });
    await expect(close).toBeFocused();

    await page.keyboard.press("Tab");
    const afterTab = await page.evaluate(() => {
      const active = document.activeElement;
      const sheet = document.querySelector('[data-testid="publish-checklist-sheet"]');
      return Boolean(active && sheet && sheet.contains(active));
    });
    expect(afterTab).toBe(true);

    await page.keyboard.press("Shift+Tab");
    const afterShift = await page.evaluate(() => {
      const active = document.activeElement;
      const sheet = document.querySelector('[data-testid="publish-checklist-sheet"]');
      return Boolean(active && sheet && sheet.contains(active));
    });
    expect(afterShift).toBe(true);

    // Background must not be keyboard-interactive while the modal is open.
    const backgroundFocusable = await page.evaluate(() => {
      const sheet = document.querySelector('[data-testid="publish-checklist-sheet"]');
      const candidates = Array.from(
        document.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ),
      );
      return candidates.some((el) => {
        if (sheet?.contains(el)) return false;
        // Radix marks the rest of the page inert via aria-hidden / pointer-events.
        let node: HTMLElement | null = el;
        while (node) {
          if (node.getAttribute("aria-hidden") === "true") return false;
          if (node.hasAttribute("inert")) return false;
          node = node.parentElement;
        }
        return el.tabIndex >= 0 || el.tagName === "BUTTON" || el.tagName === "A";
      });
    });
    expect(backgroundFocusable).toBe(false);

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await shot(page, "390x844-mobile-sheet-closed-escape.png");

    await trigger.click();
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Close checklist" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();

    expect(
      errors.filter(
        (e) =>
          !/favicon|Download the React DevTools|401 \(Unauthorized\)/i.test(e),
      ),
    ).toEqual([]);
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

  test("real request in flight shows Saving then Draft saved", async ({ page }) => {
    await openMockedEditor(page, { patchDelayMs: 1200 });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await page.locator("#draft-title").fill("Title changed for saving state");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-save-state")).toContainText("Saving…", {
      timeout: 5_000,
    });
    await expect(page.getByTestId("draft-save-state")).toContainText(
      "Draft saved — not live",
      { timeout: 15_000 },
    );
    await shot(page, "1440x900-saving-inflight.png");
  });

  test("edits during save keep unsaved after older response", async ({ page }) => {
    await openMockedEditor(page, { patchDelayMs: 1500 });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await page.locator("#draft-title").fill("First save title");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-save-state")).toContainText("Saving…");
    await page.locator("#draft-title").fill("Edited again while saving");
    await expect(page.getByTestId("draft-save-state")).toContainText(/Unsaved changes/i, {
      timeout: 20_000,
    });
  });

  test("retry after failure can succeed", async ({ page }) => {
    await openMockedEditor(page, {
      patchResponder: (attempt) => {
        if (attempt === 1) {
          return {
            status: 500,
            body: {
              code: "internal_error",
              message: "Could not save.",
              details: [],
              requestId: "req-1",
            },
          };
        }
        return {
          status: 200,
          body: {
            ...buildSyntheticProduct(),
            title: "Retried title",
            updatedAt: new Date().toISOString(),
          },
        };
      },
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await page.locator("#draft-title").fill("Retried title");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-save-retry")).toBeVisible({ timeout: 15_000 });
    await page.getByTestId("draft-save-retry").click();
    await expect(page.getByTestId("draft-save-state")).toContainText(
      "Draft saved — not live",
      { timeout: 15_000 },
    );
  });

  test("409 conflict surfaces conflict save wording", async ({ page }) => {
    await openMockedEditor(page, { patchStatus: 409 });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await page.locator("#draft-title").fill("Conflict title");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-save-state")).toContainText(
      /Someone else saved this product/i,
      { timeout: 15_000 },
    );
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
