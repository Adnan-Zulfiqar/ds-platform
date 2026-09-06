/**
 * UX-L2B-R3 — visual + accessibility evidence for Review & publish states.
 *
 * Screenshots land outside Git under UX_L2B_R3_SHOT_ROOT.
 * Uses mocked API routes (provider boundary fake); does not call Shopify.
 */
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

import {
  DEMO_STORE_ID,
  mockPublishReadiness,
  openMockedEditor,
} from "./helpers/editor-fixture";

const SHOT_ROOT =
  process.env.UX_L2B_R3_SHOT_ROOT ??
  "C:\\Users\\profe\\DropPilotLogs\\ux-l2b-r3\\shots";

async function openReview(page: Page) {
  await page.getByTestId("editor-tab-publishing").click();
  await expect(page.getByTestId("publishing-panel")).toBeVisible();
}

async function selectDemoStore(page: Page) {
  await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
}

async function shot(page: Page, name: string) {
  const file = path.join(SHOT_ROOT, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

async function stubReadiness(page: Page, overrides: Record<string, unknown>) {
  await page.route("**/api/v1/integrations/shopify/publish-readiness", async (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(mockPublishReadiness(overrides)),
    }),
  );
}

test.describe("UX-L2B-R3 visual and a11y evidence", () => {
  test.use({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });

  test("capture publish panel states and inspect a11y cues", async ({ page }) => {
    test.setTimeout(180_000);
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });

    await openReview(page);
    await shot(page, "01-choose-store");

    let releaseCheck: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      releaseCheck = resolve;
    });
    await page.route("**/api/v1/integrations/shopify/publish-readiness", async (route) => {
      await gate;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockPublishReadiness({ canPublish: true })),
      });
    });
    await selectDemoStore(page);
    await shot(page, "02-checking");
    releaseCheck?.();

    await stubReadiness(page, {
      canPublish: false,
      blockers: [
        {
          code: "store_disconnected",
          message:
            "This Shopify store is not connected. Reconnect it in Settings before publishing.",
          field: "storeId",
          section: "publishing",
          action: "Open Integrations",
        },
      ],
      recommendations: [],
    });
    await page.reload();
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-blockers")).toBeVisible({ timeout: 15_000 });
    await shot(page, "03-one-blocker");

    await stubReadiness(page, {
      canPublish: false,
      blockers: [
        {
          code: "store_disconnected",
          message: "This Shopify store is not connected.",
          field: "storeId",
          section: "publishing",
          action: "Open Integrations",
        },
        {
          code: "destination_mismatch",
          message: "This draft was imported for a different market.",
          field: null,
          section: "shipping",
          action: "Go to Shipping",
        },
      ],
      recommendations: [],
    });
    await page.reload();
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-blockers")).toBeVisible({ timeout: 15_000 });
    await shot(page, "04-multiple-blockers");

    await expect(page.getByTestId("publish-to-store")).toBeDisabled();
    await expect(page.getByTestId("publish-disabled-reason")).toBeVisible();
    const blockerBox = page.getByTestId("publish-blockers");
    await expect(blockerBox).not.toContainText(/score/i);
    await expect(blockerBox).not.toContainText(/\{"/);

    // Blocker action moves focus to the named section
    await page.getByRole("button", { name: "Go to Shipping" }).click();
    await expect(page.getByTestId("editor-tab-shipping")).toHaveAttribute(
      "aria-selected",
      "true",
    );

    await stubReadiness(page, {
      canPublish: true,
      blockers: [],
      recommendations: [
        {
          code: "title_thin",
          message: "A longer title usually helps shoppers find this product.",
          field: "title",
          section: "basics",
          action: "Edit title",
        },
      ],
    });
    await page.reload();
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-advice")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("publish-to-store")).toBeEnabled();
    await shot(page, "05-advice-only");

    await stubReadiness(page, {
      canPublish: true,
      blockers: [],
      recommendations: [],
    });
    await page.reload();
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 15_000 });
    await shot(page, "06-no-blockers");

    await page.route("**/api/v1/integrations/shopify/publish-readiness", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          code: "internal_error",
          message: "Temporary failure",
          details: [],
          requestId: "r3visual",
        }),
      }),
    );
    await page.reload();
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-to-store")).toBeDisabled({ timeout: 15_000 });
    await shot(page, "07-readiness-unavailable");

    await openMockedEditor(page, {
      patchStatus: 500,
      patchBody: {
        code: "internal_error",
        message: "Save failed",
        details: [],
        requestId: "r3savefail",
      },
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await stubReadiness(page, { canPublish: true, blockers: [], recommendations: [] });
    await page.getByLabel("Title").fill("Dirty after failed save");
    await openReview(page);
    await selectDemoStore(page);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-error")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("publish-error")).not.toContainText(/\{"/);
    await expect(page.getByTestId("publish-error")).toContainText(
      /couldn’t save|could not save|not published/i,
    );
    await shot(page, "08-save-failure");

    // 10 — dirty after a failed save: stay on Review & publish (title field is
    // on Product details). Assert no success claim and keep the failure summary.
    await expect(page.getByTestId("publish-ok")).toHaveCount(0);
    await expect(page.getByTestId("publish-error")).toBeVisible();
    await expect(page.getByTestId("publish-error")).toContainText(
      /couldn’t save|could not save|not published/i,
    );
    await shot(page, "10-dirty-after-save");
  });

  test("conflict, publishing, success, provider rejection", async ({ page }) => {
    test.setTimeout(120_000);

    await openMockedEditor(page, {
      patchStatus: 409,
      patchBody: {
        code: "conflict",
        message: "This draft changed somewhere else.",
        details: [{ type: "reason", message: "draft_version_stale" }],
        requestId: "r3conflict",
      },
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await stubReadiness(page, { canPublish: true, blockers: [], recommendations: [] });
    await page.getByLabel("Title").fill("Conflict title change");
    await openReview(page);
    await selectDemoStore(page);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-error")).toContainText(/changed somewhere else/i, {
      timeout: 15_000,
    });
    await shot(page, "09-conflict-409");

    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await stubReadiness(page, { canPublish: true, blockers: [], recommendations: [] });
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      await new Promise((r) => setTimeout(r, 1200));
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
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 15_000 });
    const pending = page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-to-store")).toContainText(/Publishing/i, {
      timeout: 5_000,
    });
    await shot(page, "11-publishing-in-progress");
    await pending;
    await expect(page.getByTestId("publish-ok")).toBeVisible({ timeout: 15_000 });
    await shot(page, "14-successful-publish");

    // 12 — busy wording (direct error path)
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await stubReadiness(page, { canPublish: true, blockers: [], recommendations: [] });
    await page.route("**/api/v1/integrations/shopify/publish", (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "shopify_publish_busy",
          message: "Publishing is already in progress. Please try again in a moment.",
          details: [],
          requestId: "r3busy",
        }),
      }),
    );
    await openReview(page);
    await selectDemoStore(page);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-error")).toContainText(/already in progress/i, {
      timeout: 15_000,
    });
    await shot(page, "12-publish-busy");

    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await stubReadiness(page, { canPublish: true, blockers: [], recommendations: [] });
    await page.route("**/api/v1/integrations/shopify/publish", (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          code: "validation_error",
          message: "Shopify refused this product. Check the title and try again.",
          details: [{ type: "reason", message: "publish_blocked" }],
          requestId: "r3reject",
        }),
      }),
    );
    await openReview(page);
    await selectDemoStore(page);
    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("publish-error")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("publish-error")).not.toContainText(/\{"/);
    await shot(page, "13-provider-rejection");
  });

  test("viewport and dark mode matrix", async ({ page }) => {
    test.setTimeout(120_000);
    const blockerStub = {
      canPublish: false,
      blockers: [
        {
          code: "store_disconnected",
          message: "This Shopify store is not connected.",
          field: "storeId",
          section: "publishing",
          action: "Open Integrations",
        },
      ],
      recommendations: [
        {
          code: "images_missing",
          message: "Add at least one image before you publish.",
          field: "images",
          section: "media",
          action: "Go to Media",
        },
      ],
    };

    for (const [name, size] of [
      ["15-mobile-blocker-390", { width: 390, height: 844 }],
      ["17-320x844", { width: 320, height: 844 }],
      ["18-390x844", { width: 390, height: 844 }],
      ["19-1024x768", { width: 1024, height: 768 }],
      ["20-1440x900", { width: 1440, height: 900 }],
    ] as const) {
      await page.setViewportSize(size);
      await openMockedEditor(page);
      await stubReadiness(page, blockerStub);
      await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
      await openReview(page);
      await selectDemoStore(page);
      await expect(page.getByTestId("publish-blockers")).toBeVisible({ timeout: 15_000 });
      const overflow = await page.evaluate(() => {
        const doc = document.documentElement;
        return doc.scrollWidth > doc.clientWidth + 1;
      });
      expect(overflow, `${name} horizontal overflow`).toBe(false);
      await shot(page, name);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await openMockedEditor(page);
    await stubReadiness(page, {
      canPublish: true,
      blockers: [],
      recommendations: [
        {
          code: "description_empty",
          message: "Add a description so shoppers know what they are buying.",
          field: "description",
          section: "basics",
          action: "Edit description",
        },
      ],
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await selectDemoStore(page);
    await expect(page.getByTestId("publish-advice")).toBeVisible({ timeout: 15_000 });
    await shot(page, "16-mobile-advice-only");

    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await openMockedEditor(page);
    await stubReadiness(page, blockerStub);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await openReview(page);
    await selectDemoStore(page);
    await shot(page, "21-dark-mode");
  });
});
