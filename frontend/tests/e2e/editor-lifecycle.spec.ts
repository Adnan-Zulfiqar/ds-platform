import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import {
  buildSyntheticProduct,
  DEMO_PRODUCT_ID,
  DEMO_STORE_ID,
  demoPublishResult,
  openMockedEditor,
  syncedDemoListing,
} from "./helpers/editor-fixture";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import { captureEvidenceScreenshot } from "./helpers/screenshot-evidence";

/**
 * Editor lifecycle clarity (UX-L2D-05) — backend-less.
 *
 * Every state the header, mobile bar, post-publish panel and Review &
 * publish can show is reached through real responses from the mocked API:
 * listing rows, delayed or failed listings requests, a publish that
 * succeeds or fails, a save that conflicts. Nothing is asserted from a
 * state the fixture could not have produced. Adapted from the reviewed
 * historical `ux-l2c-live-state` / `ux-l2c-r3-listings-status` specs
 * (UX-L2D-GATE-04) onto the current L2D fixture: the shell mocks are the
 * L2D ones; only listing/publish responders were added.
 *
 * The M2A conflict protocol is exercised only as far as presentation goes —
 * banner focus, the paused-save wording, the disabled publish — and the
 * protocol assertions stay in `draft-editor-concurrency.spec.ts` and
 * `draft-editor-real-conflict.spec.ts`, which this milestone does not edit.
 */

const SHOT_ROOT = resolveSuiteShotRoot("ux-l2d-05-editor", ["UX_L2D_05_SHOT_ROOT"]);

async function shot(page: Page, name: string) {
  await captureEvidenceScreenshot(page, name, { root: SHOT_ROOT, fullPage: false });
}

function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

async function editorReady(page: Page) {
  await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
}

async function openReview(page: Page) {
  await page.getByTestId("editor-tab-publishing").click();
  await expect(page.getByTestId("publishing-panel")).toBeVisible();
}

async function assertNoHorizontalOverflow(page: Page) {
  // The shell's <main> is its own scroll container, so check it as well as
  // the document — an overflowing editor would scroll <main> sideways while
  // the document stayed put.
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    const main = document.getElementById("main-content");
    return (
      doc.scrollWidth > doc.clientWidth + 1 ||
      (main !== null && main.scrollWidth > main.clientWidth + 1)
    );
  });
  expect(overflow, "page must not scroll horizontally").toBe(false);
}

const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();

test.describe("Editor lifecycle — header states", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light", locale: "en-GB" });

  test("a draft with no listing: Not on Shopify, Saved in DropPilot, never 'live'", async ({ page }) => {
    await openMockedEditor(page);
    await editorReady(page);
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Not on Shopify");
    await expect(badge).toHaveAttribute("data-kind", "not-on-shopify");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Saved in DropPilot");
    await expect(page.getByTestId("product-status-group")).not.toContainText(/\blive\b/i);
    await expect(visibleTestId(page, "publish-action")).toHaveText("Review & publish");
    await expect(page.getByRole("link", { name: "Back to drafts" })).toBeVisible();
    await shot(page, "1440-light-draft-default");
  });

  test("typing shows Unsaved changes, autosave settles to Saved in DropPilot", async ({ page }) => {
    await openMockedEditor(page, { patchDelayMs: 600 });
    await editorReady(page);
    await page.locator("#draft-title").fill("Wireless Desk Lamp with USB Charging — edited");
    const save = page.getByTestId("draft-save-state");
    await expect(save).toHaveText("Unsaved changes");
    await expect(save).toHaveAttribute("data-kind", "unsaved");
    await shot(page, "1440-light-dirty");
    await expect(save).toHaveText("Saving…", { timeout: 5_000 });
    await expect(save).toHaveText("Saved in DropPilot", { timeout: 15_000 });
    await expect(save).toHaveAttribute("data-kind", "saved");
    // The badge did not change: a DropPilot save says nothing about Shopify.
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Not on Shopify");
    await shot(page, "1440-light-saved");
  });

  test("a synced listing without visibility proof is Added to Shopify, with View product", async ({ page }) => {
    await openMockedEditor(page, { listings: [syncedDemoListing({ onlineStorePublished: null })] });
    await editorReady(page);
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Added to Shopify");
    await expect(badge).toHaveAttribute("data-kind", "added-to-shopify");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Saved in DropPilot");
    await expect(page.getByTestId("product-editor-store")).toHaveText("demo-shop.myshopify.com");
    const action = visibleTestId(page, "publish-action");
    await expect(action).toHaveText("View product");
    await expect(action).toHaveAttribute("href", `/products/${DEMO_PRODUCT_ID}`);
    await expect(visibleTestId(page, "manage-in-shopify")).toHaveAttribute(
      "href",
      "https://demo-shop.myshopify.com/admin/products/8123456789",
    );
    await expect(page.getByRole("link", { name: "Back to products" })).toBeVisible();
    await shot(page, "1440-light-synced-listing");
  });

  test("Visible on your shop only when onlineStorePublished is true", async ({ page }) => {
    await openMockedEditor(page, { listings: [syncedDemoListing({ onlineStorePublished: true })] });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop");
    await expect(page.getByTestId("product-lifecycle")).toHaveAttribute("data-kind", "visible-on-shop");
    await shot(page, "1440-light-visible-listing");
  });

  test("onlineStorePublished false reads Added to Shopify, never visible", async ({ page }) => {
    await openMockedEditor(page, { listings: [syncedDemoListing({ onlineStorePublished: false })] });
    await editorReady(page);
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Added to Shopify");
    await expect(badge).toHaveAttribute("data-kind", "visibility-setup-needed");
    await expect(page.getByTestId("product-status-group")).not.toContainText(/visible on your shop/i);
  });

  test("a saved draft newer than the last sync is Changes not sent to Shopify, with Update Shopify", async ({
    page,
  }) => {
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ updatedAt: hoursAgo(1) }),
      listings: [syncedDemoListing({ lastSyncedAt: hoursAgo(3), onlineStorePublished: true })],
    });
    await editorReady(page);
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Changes not sent to Shopify");
    await expect(badge).toHaveAttribute("data-kind", "changes-not-sent");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Saved in DropPilot");
    await expect(visibleTestId(page, "publish-action")).toHaveText("Update Shopify");
    await shot(page, "1440-light-changes-not-sent");
  });

  test("a draft older than the last sync is only Added to Shopify — never 'up to date'", async ({ page }) => {
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ updatedAt: hoursAgo(3) }),
      listings: [syncedDemoListing({ lastSyncedAt: hoursAgo(1) })],
    });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify");
    await expect(page.getByTestId("product-status-group")).not.toContainText(/up to date|live/i);
  });

  test("editing a published product: Unsaved changes, then Changes not sent after autosave", async ({ page }) => {
    const synced = hoursAgo(1);
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ updatedAt: hoursAgo(2) }),
      listings: [syncedDemoListing({ lastSyncedAt: synced, onlineStorePublished: true })],
      patchResponder: () => ({
        status: 200,
        body: { ...buildSyntheticProduct(), title: "Edited after publish", updatedAt: new Date().toISOString() },
      }),
    });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop");
    await page.locator("#draft-title").fill("Edited after publish");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Unsaved changes");
    await expect(visibleTestId(page, "publish-action")).toHaveText("Update Shopify");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Saved in DropPilot", { timeout: 15_000 });
    // The new version token is newer than the listing's sync — provable.
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Changes not sent to Shopify");
    await expect(visibleTestId(page, "publish-action")).toHaveText("Update Shopify");
  });

  test("a listing row in error with no synced row is Publish failed, without raw provider text", async ({ page }) => {
    await openMockedEditor(page, {
      listings: [syncedDemoListing({ status: "error", lastError: "HTTP 422 from Shopify: {\"errors\":{}}" })],
    });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Publish failed");
    await expect(page.getByTestId("product-lifecycle")).toHaveAttribute("data-kind", "publish-failed");
    await expect(page.getByText(/HTTP 422|"errors"/)).toHaveCount(0);
    await expect(visibleTestId(page, "publish-action")).toHaveText("Try publishing again");
    await expect(page.getByTestId("product-editor-store")).toHaveText("Store needs attention");
    await shot(page, "1440-light-publish-failed-listing");
  });

  test("long title and long store domain stay within the header", async ({ page }) => {
    await openMockedEditor(page, {
      product: buildSyntheticProduct({
        title:
          "Ultra Premium Extra Long Product Title That Goes On And On For Merchants Who Like To Describe Every Single Feature In The Name",
        supplierName: "A Supplier With A Considerably Long Trading Name Limited",
      }),
      listings: [
        syncedDemoListing({
          shopDomain: "a-very-long-store-name-for-the-demo-workspace-limited.myshopify.com",
          adminUrl: "https://a-very-long-store-name-for-the-demo-workspace-limited.myshopify.com/admin/products/1",
          storefrontUrl: null,
        }),
      ],
    });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify");
    await assertNoHorizontalOverflow(page);
    await shot(page, "1440-light-long-title-and-domain");
  });
});

test.describe("Editor lifecycle — listings honesty", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("a slow listings request shows Checking, never Not on Shopify", async ({ page }) => {
    await openMockedEditor(page, { listingsDelayMs: 3_000, listings: [] });
    await editorReady(page);
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Checking Shopify status…");
    await expect(badge).toHaveText("Not on Shopify", { timeout: 15_000 });
  });

  test("a failed listings request is unavailable with a retry, not Not on Shopify", async ({ page }) => {
    await openMockedEditor(page, { listingsHttpStatus: 500 });
    await editorReady(page);
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Shopify status unavailable", { timeout: 15_000 });
    await expect(badge).toHaveAttribute("data-kind", "unavailable");
    await expect(page.getByTestId("shopify-status-retry")).toBeVisible();
    await expect(page.getByText(/Could not load listings|internal_error/)).toHaveCount(0);
    await shot(page, "1440-light-listings-unavailable");
  });

  test("retry after an unavailable status recovers to the listing's real state", async ({ page }) => {
    await openMockedEditor(page, {
      listingsResponder: (attempt) =>
        attempt === 1
          ? { status: 500 }
          : { status: 200, body: [syncedDemoListing({ onlineStorePublished: true })] },
    });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Shopify status unavailable", {
      timeout: 15_000,
    });
    const retry = page.getByTestId("shopify-status-retry");
    const box = await retry.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    await retry.click();
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop", { timeout: 15_000 });
    await expect(page.getByTestId("shopify-status-retry")).toHaveCount(0);
  });

  test("a failed refresh keeps the last known state and says the refresh failed", async ({ page }) => {
    // The listings load once (a product already visible on the shop); the
    // refetch the editor triggers after a successful publish then fails.
    // The badge must not regress to "not on Shopify" or "unavailable", and
    // the failure must be said out loud with a retry.
    let attempts = 0;
    await openMockedEditor(page, {
      product: buildSyntheticProduct({ updatedAt: hoursAgo(3) }),
      listingsResponder: (attempt) => {
        attempts = attempt;
        return attempt === 1
          ? { status: 200, body: [syncedDemoListing({ onlineStorePublished: true, lastSyncedAt: hoursAgo(1) })] }
          : { status: 500 };
      },
      publishResponder: () => ({
        status: 200,
        body: demoPublishResult({
          onlineStorePublished: true,
          storefrontUrl: "https://demo-shop.myshopify.com/products/wireless-desk-lamp",
        }),
      }),
    });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop");
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("shopify-status-note")).toContainText(/couldn.t refresh/i, {
      timeout: 15_000,
    });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop");
    await expect(page.getByTestId("shopify-status-retry")).toBeVisible();
    await expect(page.getByTestId("post-publish-headline")).toHaveText("Published — visible on your shop");
    expect(attempts).toBeGreaterThanOrEqual(2);
    await shot(page, "1440-light-refresh-failed-keeps-state");
  });
});

test.describe("Editor lifecycle — publish flow", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("publishing: Sending to Shopify → Published to Shopify; header and panel agree; no 'Draft — not live'", async ({
    page,
  }) => {
    await openMockedEditor(page, {
      publishResponder: () => ({ status: 200, delayMs: 1_200, body: demoPublishResult({ onlineStorePublished: null }) }),
    });
    await editorReady(page);
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-status-summary")).toContainText(/Validation passed/i, {
      timeout: 10_000,
    });
    await expect(page.getByTestId("post-publish-success")).toHaveCount(0);
    await expect(page.getByTestId("publish-to-store")).toHaveText("Publish to Store");
    await shot(page, "1440-light-review-validation-passed");

    await page.getByTestId("publish-to-store").click();
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Sending to Shopify…");
    await expect(badge).toHaveAttribute("data-kind", "publishing");
    await expect(visibleTestId(page, "publish-action")).toBeDisabled();
    await shot(page, "1440-light-publishing");

    await expect(page.getByTestId("post-publish-success")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("post-publish-headline")).toHaveText("Published to Shopify");
    await expect(badge).toHaveText("Added to Shopify");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Saved in DropPilot");
    await expect(page.getByTestId("draft-editor")).not.toContainText(/not live|Draft — /i);
    await expect(page.getByTestId("draft-editor")).not.toContainText(/write_publications/);
    await expect(page.getByTestId("post-publish-view-product")).toHaveAttribute("href", `/products/${DEMO_PRODUCT_ID}`);
    await expect(page.getByTestId("post-publish-storefront")).toHaveCount(0);
    await expect(page.getByTestId("publish-to-store")).toHaveText("Update Shopify");
    await expect(visibleTestId(page, "publish-action")).toHaveText("View product");
    await expect(page.getByRole("link", { name: "Back to products" })).toBeVisible();
    await shot(page, "1440-light-post-publish");
  });

  test("a publish response confirming visibility says so, and links the storefront", async ({ page }) => {
    await openMockedEditor(page, {
      publishResponder: () => ({
        status: 200,
        body: demoPublishResult({
          onlineStorePublished: true,
          storefrontUrl: "https://demo-shop.myshopify.com/products/wireless-desk-lamp",
        }),
      }),
    });
    await editorReady(page);
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("post-publish-headline")).toHaveText("Published — visible on your shop", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop");
    await expect(page.getByTestId("post-publish-storefront")).toHaveAttribute(
      "href",
      "https://demo-shop.myshopify.com/products/wireless-desk-lamp",
    );
    await expect(page.getByTestId("post-publish-storefront")).toHaveAttribute("rel", "noopener noreferrer");
  });

  test("a publish response with visibility false explains setup, without claiming visibility", async ({
    page,
  }) => {
    await openMockedEditor(page, {
      publishResponder: () => ({
        status: 200,
        body: demoPublishResult({
          onlineStorePublished: false,
          storefrontUrl: "http://demo-shop.myshopify.com/products/wireless-desk-lamp",
        }),
      }),
    });
    await editorReady(page);
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("post-publish-headline")).toHaveText("Published to Shopify", { timeout: 15_000 });
    await expect(page.getByTestId("post-publish-detail")).toContainText(/not visible on your online shop yet/i);
    await expect(page.getByTestId("product-lifecycle")).toHaveAttribute("data-kind", "visibility-setup-needed");
    // An http:// storefront URL is never linked.
    await expect(page.getByTestId("post-publish-storefront")).toHaveCount(0);
    await page.getByTestId("post-publish-details-toggle").click();
    await expect(page.getByTestId("post-publish-details")).toContainText("8123456789");
    await shot(page, "1440-light-post-publish-visibility-setup");
  });

  test("a failed publish is Publish failed in the header and an error in the panel, never both green", async ({
    page,
  }) => {
    await openMockedEditor(page, {
      publishResponder: () => ({
        status: 502,
        body: { code: "shopify_error", message: "Shopify did not accept the product. Try again in a moment.", details: [], requestId: "r" },
      }),
    });
    await editorReady(page);
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-error")).toContainText(/did not accept/i, { timeout: 15_000 });
    const badge = page.getByTestId("product-lifecycle");
    await expect(badge).toHaveText("Publish failed");
    await expect(badge).toHaveAttribute("data-kind", "publish-failed");
    await expect(page.getByTestId("post-publish-success")).toHaveCount(0);
    await expect(page.getByTestId("publish-ok")).toHaveCount(0);
    await expect(visibleTestId(page, "publish-action")).toHaveText("Try publishing again");
    await expect(page.getByText(/"code"|shopify_error/)).toHaveCount(0);
    await shot(page, "1440-light-publish-failed");
  });

  test("server blockers name the editor section, not the API key", async ({ page }) => {
    await openMockedEditor(page);
    await editorReady(page);
    await page.route("**/api/v1/integrations/shopify/publish-readiness", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          channel: "shopify",
          storeId: DEMO_STORE_ID,
          draftId: DEMO_PRODUCT_ID,
          draftUpdatedAt: new Date().toISOString(),
          canPublish: false,
          blockers: [
            { code: "title_thin", message: "Add a clearer product title.", field: "title", section: "basics", action: "Edit title" },
          ],
          recommendations: [
            { code: "x_unknown", message: "Something advisory.", field: null, section: "not-a-section", action: null },
          ],
          checkedAt: new Date().toISOString(),
        }),
      }),
    );
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-blockers")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("publish-blocker-item")).toContainText("In Product details");
    await expect(page.getByTestId("publish-blocker-item")).not.toContainText(/Section: basics/);
    await expect(page.getByTestId("publish-advice-item")).not.toContainText(/not-a-section/);
    await expect(page.getByTestId("publish-to-store")).toBeDisabled();
    await page.getByRole("button", { name: "Edit title" }).click();
    await expect(page.getByTestId("editor-tab-overview")).toHaveAttribute("aria-selected", "true");
  });
});

test.describe("Editor lifecycle — conflict presentation", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("a 409 moves focus to the banner once, pauses saving in words, and disables publish", async ({ page }) => {
    await openMockedEditor(page, { patchStatus: 409 });
    await editorReady(page);
    await page.locator("#draft-title").fill("Conflicting title");
    await visibleTestId(page, "save-draft").click();

    const banner = page.getByTestId("draft-conflict-banner");
    await expect(banner).toBeVisible({ timeout: 15_000 });
    await expect(banner).toBeFocused();
    await expect(page.getByTestId("draft-save-state")).toHaveText(/Saving paused — someone else saved this product/);
    await expect(page.getByTestId("draft-save-state")).toHaveAttribute("data-kind", "conflict");
    await expect(visibleTestId(page, "publish-action")).toHaveText("Resolve conflict");
    // Manual Save is withdrawn while the conflict is open; the banner's two
    // actions are the only way forward.
    await expect(visibleTestId(page, "save-draft")).toHaveCount(0);

    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-to-store")).toBeDisabled();
    await expect(page.getByTestId("publish-disabled-reason")).toContainText(/Resolve the editing conflict/i);
    await expect(page.getByTestId("publish-conflict-pointer")).toBeVisible();
    await shot(page, "1440-light-conflict");

    // Opening and closing Review returns focus to its trigger — the banner
    // effect must not steal it back on the `reviewing → detected` change.
    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("conflict-review-dialog")).toHaveCount(0);
    await expect(page.getByTestId("conflict-review-mine")).toBeFocused();
    await expect(banner).not.toBeFocused();

    // The header's Resolve conflict control brings the banner back into focus.
    await visibleTestId(page, "publish-action").click();
    await expect(banner).toBeFocused();
    // The merchant's text is untouched throughout (the M2A suites cover the
    // protocol; this is the presentation pass).
    await page.getByTestId("editor-tab-overview").click();
    await expect(page.getByTestId("draft-title-input")).toHaveValue("Conflicting title");
  });
});

test.describe("Editor lifecycle — section navigation", () => {
  test("at 1024 the strip scrolls with fades and chevrons, and a deep link lands on its tab", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await openMockedEditor(page);
    await editorReady(page);
    const strip = page.getByTestId("product-editor-tabs");
    await expect(strip).toHaveAttribute("data-scroll-right", "true");
    await expect(strip).toHaveAttribute("data-scroll-left", "false");
    await expect(page.getByTestId("editor-tabs-scroll-right")).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await shot(page, "1024-light-tabs-start");

    await page.getByTestId("editor-tabs-scroll-right").click();
    await expect(strip).toHaveAttribute("data-scroll-left", "true", { timeout: 5_000 });

    await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=publishing`);
    await expect(page.getByTestId("publishing-panel")).toBeVisible({ timeout: 30_000 });
    const publishTab = page.getByTestId("editor-tab-publishing");
    await expect(publishTab).toHaveAttribute("aria-selected", "true");
    // The strip is scrolled by an effect after hydration; the panel above is
    // visible from the server render, so poll rather than measure once.
    await expect
      .poll(
        async () => {
          const [tabBox, stripBox] = await Promise.all([publishTab.boundingBox(), strip.boundingBox()]);
          return Boolean(
            tabBox &&
              stripBox &&
              tabBox.x >= stripBox.x - 1 &&
              tabBox.x + tabBox.width <= stripBox.x + stripBox.width + 1,
          );
        },
        { timeout: 10_000 },
      )
      .toBe(true);
    await shot(page, "1024-light-tabs-deep-link");
  });

  test("keyboard: End reaches Review & publish and scrolls it into view", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await openMockedEditor(page);
    await editorReady(page);
    await page.getByTestId("editor-tab-overview").focus();
    await page.keyboard.press("End");
    await expect(page.getByTestId("editor-tab-publishing")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("editor-tab-publishing")).toBeFocused();
    await expect(page.getByTestId("product-editor-tabs")).toHaveAttribute("data-scroll-left", "true");
    await page.keyboard.press("Home");
    await expect(page.getByTestId("editor-tab-overview")).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("product-editor-tabs")).toHaveAttribute("data-scroll-left", "false");
  });

  test("at 1024 the actions row sits under the identity, keeping the title readable", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await openMockedEditor(page);
    await editorReady(page);
    await expect(visibleTestId(page, "publish-action")).toHaveCount(1);
    await expect(visibleTestId(page, "product-editor-actions")).toHaveCount(1);
    const [title, actions] = await Promise.all([
      page.getByTestId("product-editor-title").boundingBox(),
      visibleTestId(page, "product-editor-actions").boundingBox(),
    ]);
    expect(title && actions && actions.y >= title.y + title.height).toBe(true);
  });
});

test.describe("Editor lifecycle — mobile", () => {
  test.use({ viewport: { width: 390, height: 844 }, colorScheme: "light" });

  test("bar, badge and save state are reachable; no overlapping bars; no overflow", async ({ page }) => {
    await openMockedEditor(page);
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toBeVisible();
    await expect(page.getByTestId("draft-save-state")).toBeVisible();
    const bar = page.getByTestId("mobile-editor-action-bar");
    await expect(bar).toBeVisible();
    const cta = bar.getByTestId("publish-action");
    await expect(cta).toHaveText("Review & publish");
    for (const target of [cta, bar.getByRole("button", { name: "Preview" })]) {
      const box = await target.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
    }
    await assertNoHorizontalOverflow(page);
    await shot(page, "390-light-default");

    await page.locator("#draft-title").fill("Edited on a phone");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Unsaved changes");
    await expect(bar.getByTestId("save-draft")).toBeVisible();
    await shot(page, "390-light-dirty");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Saved in DropPilot", { timeout: 15_000 });
    await expect(bar.getByTestId("save-draft")).toHaveCount(0);
    await shot(page, "390-light-saved");

    // Scrolled to the end, the last line of content sits above the fixed bar:
    // the editor reserves the bar's height in its own bottom padding.
    const clearance = await page.evaluate(() => {
      // The shell's <main> is the scroll container, not the window.
      const main = document.getElementById("main-content");
      if (main) main.scrollTop = main.scrollHeight;
      window.scrollTo(0, document.documentElement.scrollHeight);
      const editor = document.querySelector('[data-testid="draft-editor"]') as HTMLElement;
      const bar = document.querySelector('[data-testid="mobile-editor-action-bar"]') as HTMLElement;
      const padding = Number.parseFloat(getComputedStyle(editor).paddingBottom);
      const contentBottom = editor.getBoundingClientRect().bottom - padding;
      return bar.getBoundingClientRect().top - contentBottom;
    });
    expect(clearance).toBeGreaterThanOrEqual(0);
  });

  test("the section strip scrolls to the selected tab and shows a fade", async ({ page }) => {
    await openMockedEditor(page);
    await editorReady(page);
    const strip = page.getByTestId("product-editor-tabs");
    await expect(strip).toHaveAttribute("data-scroll-right", "true");
    await page.goto(`/drafts/${DEMO_PRODUCT_ID}?tab=seo`);
    await editorReady(page);
    await expect(page.getByTestId("editor-tab-seo")).toHaveAttribute("aria-selected", "true");
    await expect(strip).toHaveAttribute("data-scroll-left", "true");
    await assertNoHorizontalOverflow(page);
    await shot(page, "390-light-tabs-seo");
  });

  test("publishing and the post-publish panel on a phone", async ({ page }) => {
    await openMockedEditor(page, {
      publishResponder: () => ({ status: 200, delayMs: 800 }),
    });
    await editorReady(page);
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Sending to Shopify…");
    await shot(page, "390-light-publishing");
    await expect(page.getByTestId("post-publish-success")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify");
    await expect(page.getByTestId("mobile-editor-action-bar").getByTestId("publish-action")).toHaveText("View product");
    await assertNoHorizontalOverflow(page);
    await shot(page, "390-light-post-publish");
  });

  test("conflict on a phone: banner focused, bar offers Resolve conflict", async ({ page }) => {
    await openMockedEditor(page, { patchStatus: 409 });
    await editorReady(page);
    await page.locator("#draft-title").fill("Phone conflict");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("draft-conflict-banner")).toBeFocused();
    await expect(page.getByTestId("mobile-editor-action-bar").getByTestId("publish-action")).toHaveText(
      "Resolve conflict",
    );
    await expect(page.getByTestId("conflict-reload-latest")).toBeVisible();
    await expect(page.getByTestId("conflict-review-mine")).toBeVisible();
    for (const id of ["conflict-reload-latest", "conflict-review-mine"]) {
      const box = await page.getByTestId(id).boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    await assertNoHorizontalOverflow(page);
    await shot(page, "390-light-conflict");
  });
});

test.describe("Editor lifecycle — dark theme", () => {
  test.use({ colorScheme: "dark" });

  test("1440 dark: synced, changes not sent, unavailable", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openMockedEditor(page, { listings: [syncedDemoListing({ onlineStorePublished: true })] });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop");
    await shot(page, "1440-dark-visible-listing");

    await openMockedEditor(page, {
      product: buildSyntheticProduct({ updatedAt: hoursAgo(1) }),
      listings: [syncedDemoListing({ lastSyncedAt: hoursAgo(3) })],
    });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Changes not sent to Shopify");
    await shot(page, "1440-dark-changes-not-sent");

    await openMockedEditor(page, { listingsHttpStatus: 500 });
    await editorReady(page);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Shopify status unavailable", { timeout: 15_000 });
    await shot(page, "1440-dark-listings-unavailable");
  });

  test("1024 dark: conflict and tabs", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await openMockedEditor(page, { patchStatus: 409 });
    await editorReady(page);
    await page.locator("#draft-title").fill("Dark conflict");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible({ timeout: 15_000 });
    await shot(page, "1024-dark-conflict");
  });

  test("390 dark: post-publish", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openMockedEditor(page, { publishResponder: () => ({ status: 200 }) });
    await editorReady(page);
    await openReview(page);
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("post-publish-success")).toBeVisible({ timeout: 15_000 });
    await shot(page, "390-dark-post-publish");
  });
});
