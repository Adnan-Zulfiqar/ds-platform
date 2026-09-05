import { expect, test, type Page } from "@playwright/test";

import { openMockedEditor } from "./helpers/editor-fixture";

/** Prefer the currently visible control when responsive duplicates stay in the DOM. */
function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

/**
 * UX-L2A command bar — hierarchy, responsive chrome, keyboard menu.
 *
 * Uses synthetic mocked API responses so the suite never depends on AliExpress
 * import or production customer drafts.
 */

async function openDraftEditor(page: Page): Promise<void> {
  await openMockedEditor(page);
  await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
}

test.describe("Draft editor header — desktop", () => {
  test.use({
    viewport: { width: 1440, height: 1000 },
    colorScheme: "light",
  });

  test("shows identity hierarchy and primary Publish emphasis", async ({
    page,
  }) => {
    await openDraftEditor(page);

    await expect(page.getByTestId("product-editor-header")).toBeVisible();
    await expect(page.getByRole("link", { name: /Back to drafts/i })).toBeVisible();
    await expect(page.getByTestId("product-editor-title")).toBeVisible();
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Draft");
    await expect(page.getByTestId("draft-save-state")).toBeVisible();
    await expect(visibleTestId(page, "publish-action")).toHaveCount(1);

    await page.getByRole("button", { name: "Preview" }).first().click();
    await expect(page.getByTestId("draft-preview-panel")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Draft Preview" })).toBeVisible();
    await page.keyboard.press("Escape");

    await visibleTestId(page, "product-actions-menu").click();
    await expect(
      page.getByRole("menuitem", { name: /Refresh supplier information/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("menuitem", { name: /Improve with AI tools/i }),
    ).toBeVisible();
    await expect(page.getByRole("menuitem", { name: /View recent activity/i })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: /Delete Draft/i })).toHaveCount(0);

    await page.screenshot({
      path: "test-results/editor-header-desktop-light.png",
      fullPage: false,
    });
  });

  test("More menu is keyboard accessible and Escape returns focus", async ({
    page,
  }) => {
    await openDraftEditor(page);

    const more = visibleTestId(page, "product-actions-menu");
    await more.focus();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("menuitem", { name: /Refresh supplier information/i }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(more).toBeFocused();

    await page.screenshot({
      path: "test-results/editor-header-more-menu.png",
      fullPage: false,
    });
  });
});

test.describe("Draft editor header — dark desktop", () => {
  test.use({
    viewport: { width: 1440, height: 1000 },
    colorScheme: "dark",
  });

  test("renders in dark mode without overflow", async ({ page }) => {
    await openDraftEditor(page);
    await expect(page.getByTestId("product-editor-header")).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflow).toBe(false);
    await page.screenshot({
      path: "test-results/editor-header-desktop-dark.png",
      fullPage: false,
    });
  });
});

test.describe("Draft editor header — tablet", () => {
  test.use({ viewport: { width: 1024, height: 900 } });

  test("keeps Publish visible and tabs scrollable", async ({ page }) => {
    await openDraftEditor(page);
    await expect(visibleTestId(page, "publish-action")).toHaveCount(1);
    await expect(page.getByTestId("product-editor-tabs")).toBeVisible();
    await page.screenshot({
      path: "test-results/editor-header-tablet.png",
      fullPage: false,
    });
  });
});

test.describe("Draft editor header — mobile", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("uses compact header and sticky bottom actions", async ({ page }) => {
    await openDraftEditor(page);

    await expect(page.getByTestId("mobile-editor-action-bar")).toBeVisible();
    await expect(visibleTestId(page, "product-actions-menu")).toHaveCount(1);
    await expect(visibleTestId(page, "product-editor-actions")).toHaveCount(0);

    const barBox = await page.getByTestId("mobile-editor-action-bar").boundingBox();
    expect(barBox).not.toBeNull();
    expect(barBox!.height).toBeGreaterThanOrEqual(44);

    await page.screenshot({
      path: "test-results/editor-header-mobile.png",
      fullPage: false,
    });
  });
});

test.describe("Draft editor header — drafts shell smoke", () => {
  test("mocked session can reach drafts editor route", async ({ page }) => {
    await openDraftEditor(page);
    await expect(page).toHaveURL(/\/drafts\//);
  });
});
