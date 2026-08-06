import { expect, test, type Page } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";
import { seedCatalogueViaApi, signInWithAccount } from "./helpers/catalogue";

/**
 * Premium draft editor header — hierarchy, responsive chrome, keyboard menu.
 *
 * These tests exercise the DropPilot header shell. Catalogue seeding still
 * depends on AliExpress connectivity; when seeding fails the suite skips rather
 * than inventing a product fixture that would drift from production shapes.
 */

async function openSeededDraft(page: Page) {
  const apiUp = await isApiReachable();
  test.skip(!apiUp, "API not reachable at E2E_API_URL / default.");

  const seeded = await seedCatalogueViaApi(page.request);
  test.skip(
    seeded === null,
    "Catalogue seeding failed — cannot open a real draft editor.",
  );

  await signInWithAccount(page, seeded.account);
  await page.goto(`/drafts/${seeded.product.id}`);
  await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
  return seeded;
}

test.describe("Draft editor header — desktop", () => {
  test.use({
    viewport: { width: 1440, height: 1000 },
    colorScheme: "light",
  });

  test("shows identity hierarchy and primary Publish emphasis", async ({
    page,
  }) => {
    await openSeededDraft(page);

    await expect(page.getByTestId("product-editor-header")).toBeVisible();
    await expect(page.getByTestId("product-editor-breadcrumb")).toContainText(
      "Edit Product",
    );
    await expect(page.getByTestId("product-editor-title")).toBeVisible();
    await expect(page.getByTestId("supplier-sync-status")).toBeVisible();
    await expect(page.getByTestId("publish-action")).toBeVisible();
    await expect(page.getByTestId("save-draft")).toBeVisible();

    // Preview opens Draft Preview — not a silent tab switch.
    await page.getByRole("button", { name: "Preview" }).first().click();
    await expect(page.getByTestId("draft-preview-panel")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Draft Preview" })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByTestId("product-actions-menu").click();
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
    await openSeededDraft(page);

    const more = page.getByTestId("product-actions-menu");
    await more.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: /Refresh Supplier/i })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(more).toBeFocused();
  });
});

test.describe("Draft editor header — dark desktop", () => {
  test.use({
    viewport: { width: 1440, height: 1000 },
    colorScheme: "dark",
  });

  test("renders in dark mode without overflow", async ({ page }) => {
    await openSeededDraft(page);
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
    await openSeededDraft(page);
    await expect(page.getByTestId("publish-action").first()).toBeVisible();
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
    await openSeededDraft(page);

    await expect(page.getByTestId("mobile-editor-action-bar")).toBeVisible();
    await expect(page.getByTestId("product-actions-menu")).toBeVisible();
    // Desktop/tablet action clusters use responsive `hidden` classes — none visible here.
    await expect(page.getByTestId("product-editor-actions")).toHaveCount(0);

    const barBox = await page.getByTestId("mobile-editor-action-bar").boundingBox();
    expect(barBox).not.toBeNull();
    expect(barBox!.height).toBeGreaterThanOrEqual(44);

    await page.screenshot({
      path: "test-results/editor-header-mobile.png",
      fullPage: false,
    });
  });
});

test.describe("Draft editor header — registration smoke", () => {
  test("authenticated shell still reaches drafts list", async ({ page }) => {
    const apiUp = await isApiReachable();
    test.skip(!apiUp, "API not reachable.");
    await registerAndSignIn(page);
    await page.goto("/drafts");
    await expect(page).toHaveURL(/\/drafts/);
  });
});
