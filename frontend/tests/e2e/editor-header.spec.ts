import { expect, test, type Page } from "@playwright/test";

import { isApiReachable, type TestAccount } from "./helpers/auth";
import { seedCatalogueViaApi, signInWithAccount } from "./helpers/catalogue";

/** Prefer the currently visible control when responsive duplicates stay in the DOM. */
function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

/**
 * Premium draft editor header — hierarchy, responsive chrome, keyboard menu.
 *
 * Prefer a seeded AliExpress import when the live gateway allows it. Otherwise
 * `E2E_PRODUCT_ID` + `E2E_EMAIL` + `E2E_PASSWORD` open an existing draft so the
 * header shell can still be asserted without inventing product fixtures.
 *
 * Sign-in always uses `?next=` so the SPA keeps the in-memory access token
 * (hard navigations after login currently lose session until refresh-cookie
 * Path quirks are resolved).
 */

async function openDraftEditor(page: Page): Promise<void> {
  const apiUp = await isApiReachable();
  test.skip(!apiUp, "API not reachable at E2E_API_URL / default.");

  const existingId = process.env.E2E_PRODUCT_ID;
  const existingEmail = process.env.E2E_EMAIL;
  const existingPassword = process.env.E2E_PASSWORD;

  if (existingId && existingEmail && existingPassword) {
    const account: TestAccount = {
      email: existingEmail,
      password: existingPassword,
      companyName: "E2E Existing",
    };
    await signInWithAccount(page, account, `/drafts/${existingId}`);
  } else {
    const seeded = await seedCatalogueViaApi(page.request);
    test.skip(
      seeded === null,
      "Catalogue seeding failed — set E2E_PRODUCT_ID/E2E_EMAIL/E2E_PASSWORD or enable AliExpress import.",
    );
    await signInWithAccount(page, seeded.account, `/drafts/${seeded.product.id}`);
  }

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
    await expect(page.getByTestId("product-editor-breadcrumb")).toContainText(
      "Edit Product",
    );
    await expect(page.getByTestId("product-editor-title")).toBeVisible();
    await expect(page.getByTestId("supplier-sync-status")).toBeVisible();
    await expect(visibleTestId(page, "publish-action")).toHaveCount(1);
    await expect(visibleTestId(page, "save-draft")).toHaveCount(1);

    // Preview opens Draft Preview — not a silent tab switch.
    await page.getByRole("button", { name: "Preview" }).first().click();
    await expect(page.getByTestId("draft-preview-panel")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Draft Preview" })).toBeVisible();
    await page.keyboard.press("Escape");

    await visibleTestId(page, "product-actions-menu").click();
    await expect(page.getByRole("menuitem", { name: /Refresh Supplier/i })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: /Optimize with AI/i })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: /View History/i })).toBeVisible();
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
    await expect(page.getByRole("menuitem", { name: /Refresh Supplier/i })).toBeVisible();
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
    // Desktop/tablet action clusters remain in the DOM but must not be visible.
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
  test("authenticated shell still reaches drafts list", async ({ page }) => {
    const apiUp = await isApiReachable();
    test.skip(!apiUp, "API not reachable.");

    const email = process.env.E2E_EMAIL;
    const password = process.env.E2E_PASSWORD;
    test.skip(!email || !password, "E2E_EMAIL/E2E_PASSWORD required for drafts smoke.");

    await signInWithAccount(
      page,
      { email, password, companyName: "E2E Existing" },
      "/drafts",
    );
    await expect(page).toHaveURL(/\/drafts/);
  });
});
