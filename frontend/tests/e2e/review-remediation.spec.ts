import { expect, test, type Page } from "@playwright/test";

import type { ProductDetail, ProductVersion, StoreListing } from "@/types/api";

import {
  buildSyntheticProduct,
  DEMO_PRODUCT_ID,
  DEMO_STORE_ID,
  demoPublishResult,
  openMockedEditor,
  syncedDemoListing,
} from "./helpers/editor-fixture";

/**
 * Review remediation — UI half of findings E-1, I-1 and I-2.
 *
 * Route-mocked: no backend, no real Shopify. The server half of each
 * contract is enforced and tested in the backend suite; these assert that
 * the editor states it honestly and sends exactly what the merchant chose.
 */

const AI_VERSION_ID = "77777777-7777-4777-8777-777777777777";

function version(overrides: Partial<ProductVersion>): ProductVersion {
  return {
    id: "88888888-8888-4888-8888-888888888888",
    versionNumber: 1,
    source: "original",
    title: "Wireless Desk Lamp with USB Charging",
    description: null,
    active: false,
    isPipelineCandidate: false,
    aiProvider: null,
    promptExecutionId: null,
    createdByUserId: null,
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}

const VERSIONS: ProductVersion[] = [
  version({
    id: "99999999-9999-4999-8999-999999999993",
    versionNumber: 4,
    source: "ai_generated",
    title: "Pipeline candidate awaiting review",
    isPipelineCandidate: true,
  }),
  version({
    id: AI_VERSION_ID,
    versionNumber: 3,
    source: "ai_generated",
    title: "Approved AI title",
    active: true,
    isPipelineCandidate: true,
    aiProvider: "test",
  }),
  version({
    id: "99999999-9999-4999-8999-999999999992",
    versionNumber: 2,
    source: "ai_generated",
    title: "Legacy optimize title",
  }),
  version({ id: "99999999-9999-4999-8999-999999999991", versionNumber: 1 }),
];

async function mockVersions(page: Page, items: ProductVersion[] = VERSIONS) {
  await page.route(
    (url) => url.pathname.endsWith(`/api/v1/products/${DEMO_PRODUCT_ID}/versions`),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items,
          meta: {
            page: 1,
            size: 25,
            totalItems: items.length,
            totalPages: 1,
            hasNext: false,
            hasPrevious: false,
          },
        }),
      }),
  );
}

function aiListing(): StoreListing {
  return syncedDemoListing({ contentSource: "ai_version", contentVersionId: AI_VERSION_ID });
}

async function openReview(page: Page) {
  await page.getByTestId("editor-tab-publishing").click();
  await expect(page.getByTestId("publishing-panel")).toBeVisible();
  await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
}

async function openMoreMenu(page: Page) {
  await page.getByTestId("product-actions-menu").first().click();
}

test.describe("E-1 — approved AI text live on the store", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("states which text the store shows and that publishing keeps it", async ({ page }) => {
    await mockVersions(page);
    await openMockedEditor(page, { listings: [aiListing()] });
    await openReview(page);

    const notice = page.getByTestId("publish-ai-content-live");
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(notice).toContainText("Shopify shows your approved AI text");
    await expect(notice).toContainText("approved AI version 3");
    await expect(notice).toContainText("Publishing keeps them");
    await expect(page.getByTestId("publish-to-store")).toContainText(
      "Update Shopify (keep AI text)",
    );
  });

  test("an ordinary publish never asks to replace the AI text", async ({ page }) => {
    const bodies: Record<string, unknown>[] = [];
    await mockVersions(page);
    await openMockedEditor(page, {
      listings: [aiListing()],
      publishResponder: () => ({
        status: 200,
        body: demoPublishResult({ contentSource: "ai_version", contentVersionId: AI_VERSION_ID }),
      }),
    });
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      bodies.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fallback();
    });
    await openReview(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 15_000 });
    await page.getByTestId("publish-to-store").click();

    await expect(page.getByTestId("publish-ok")).toContainText(
      "Shopify kept your approved AI title and description",
      { timeout: 15_000 },
    );
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).not.toHaveProperty("replaceAiContent");
  });

  test("unreadable live AI text is explained, not treated as an edit conflict", async ({ page }) => {
    // DE-7: before this, every 409 opened the conflict review and blamed an
    // edit that never happened.
    await mockVersions(page);
    await openMockedEditor(page, {
      listings: [aiListing()],
      publishResponder: () => ({
        status: 409,
        body: {
          code: "conflict",
          message: "server text",
          details: [{ field: null, type: "reason", message: "published_ai_content_unavailable" }],
          requestId: "req-409",
        },
      }),
    });
    await openReview(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 15_000 });
    await page.getByTestId("publish-to-store").click();

    await expect(page.getByTestId("publish-error")).toContainText(
      "The approved AI text live on Shopify can no longer be read",
    );
    await expect(page.getByTestId("publish-error")).not.toContainText("changed somewhere else");
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
  });

  test("a publish with no reply is reported as unknown and the listing is re-read", async ({ page }) => {
    let listingReads = 0;
    await mockVersions(page);
    await openMockedEditor(page, { listings: [aiListing()] });
    await page.route("**/api/v1/integrations/shopify/publish", (route) => route.abort("connectionreset"));
    await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}/listings`, (route) => {
      listingReads += 1;
      return route.fallback();
    });
    await openReview(page);
    await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 15_000 });
    const readsBefore = listingReads;
    await page.getByTestId("publish-to-store").click();

    await expect(page.getByTestId("publish-error")).toContainText("The publish may still have completed");
    await expect(page.getByTestId("publish-error")).not.toContainText("failed");
    await expect.poll(() => listingReads).toBeGreaterThan(readsBefore);
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
  });

  test("replacing the AI text needs an explicit confirmation", async ({ page }) => {
    const bodies: Record<string, unknown>[] = [];
    await mockVersions(page);
    await openMockedEditor(page, {
      listings: [aiListing()],
      publishResponder: () => ({ status: 200, body: demoPublishResult({ contentSource: "product" }) }),
    });
    await page.route("**/api/v1/integrations/shopify/publish", async (route) => {
      bodies.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fallback();
    });
    await openReview(page);
    await expect(page.getByTestId("replace-ai-content-open")).toBeEnabled({ timeout: 15_000 });

    // Opening and cancelling sends nothing.
    await page.getByTestId("replace-ai-content-open").click();
    const dialog = page.getByTestId("replace-ai-content-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Replace the AI text on Shopify?");
    await expect(dialog).toContainText("approved AI version 3");
    await page.getByTestId("replace-ai-content-cancel").click();
    await expect(dialog).toBeHidden();
    expect(bodies).toHaveLength(0);

    // Confirming sends exactly one publish that asks for the replacement.
    await page.getByTestId("replace-ai-content-open").click();
    await page.getByTestId("replace-ai-content-confirm").click();
    await expect(page.getByTestId("publish-ok")).toContainText(
      "Shopify now shows your draft title and description",
      { timeout: 15_000 },
    );
    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({ replaceAiContent: true, storeId: DEMO_STORE_ID });
  });

  test("a store showing draft text offers no replacement control", async ({ page }) => {
    await openMockedEditor(page, { listings: [syncedDemoListing({ contentSource: "product" })] });
    await openReview(page);
    await expect(page.getByTestId("publish-to-store")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("publish-ai-content-live")).toHaveCount(0);
    await expect(page.getByTestId("replace-ai-content-open")).toHaveCount(0);
  });
});

test.describe("I-1 — editor actions never cause a surprise conflict", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a clean editor adopts the token Optimize returns", async ({ page }) => {
    const product = buildSyntheticProduct();
    const afterOptimize = new Date(Date.parse(product.updatedAt) + 5_000).toISOString();
    const patchTokens: string[] = [];
    await openMockedEditor(page, { product });
    await page.route(`**/api/v1/products/${DEMO_PRODUCT_ID}/optimize`, (route) =>
      route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          product: { ...product, updatedAt: afterOptimize } satisfies ProductDetail,
          version: version({ versionNumber: 2, source: "ai_generated", active: true }),
        }),
      }),
    );
    await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}`, async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON() as { expectedUpdatedAt?: string };
        patchTokens.push(body.expectedUpdatedAt ?? "");
      }
      return route.fallback();
    });

    await openMoreMenu(page);
    const optimized = page.waitForResponse((response) => response.url().endsWith("/optimize"));
    await page.getByRole("menuitem", { name: /Improve with AI tools/i }).click();
    await optimized;
    await expect(page.getByTestId("product-actions-menu").first()).toBeEnabled();
    await expect(page.getByTestId("editor-product-action-notice")).toHaveCount(0);

    await page.getByLabel("Title").fill("Merchant edit after optimizing");
    await expect.poll(() => patchTokens.length, { timeout: 15_000 }).toBeGreaterThan(0);
    expect(patchTokens[0]).toBe(afterOptimize);
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
  });

  test("a dirty editor refuses Optimize and sends nothing", async ({ page }) => {
    let optimizeCalls = 0;
    // Hold the save so the editor stays dirty while we act.
    await openMockedEditor(page, { patchDelayMs: 10_000 });
    await page.route(`**/api/v1/products/${DEMO_PRODUCT_ID}/optimize`, (route) => {
      optimizeCalls += 1;
      return route.fulfill({ status: 500, body: "{}" });
    });

    await page.getByLabel("Title").fill("Unsaved merchant edit");
    await openMoreMenu(page);
    await page.getByRole("menuitem", { name: /Improve with AI tools/i }).click();

    await expect(page.getByTestId("editor-product-action-notice")).toContainText(
      "Save your changes first",
    );
    expect(optimizeCalls).toBe(0);
  });

  test("a dirty editor disables Activate in version history", async ({ page }) => {
    await mockVersions(page);
    await openMockedEditor(page, { patchDelayMs: 10_000 });
    await page.getByLabel("Title").fill("Unsaved merchant edit");
    await openMoreMenu(page);
    await page.getByRole("menuitem", { name: /Version history/i }).click();

    const rows = page.getByTestId("product-version-row");
    await expect(rows).toHaveCount(4, { timeout: 15_000 });
    const legacy = rows.filter({ hasText: "Legacy optimize title" });
    await expect(legacy.getByTestId("version-activate")).toBeDisabled();
    await expect(legacy.getByTestId("version-activate-blocked")).toContainText(
      "Save your changes first",
    );
  });

  test("a clean editor adopts the token Activate returns", async ({ page }) => {
    const product = buildSyntheticProduct();
    const afterActivate = new Date(Date.parse(product.updatedAt) + 7_000).toISOString();
    const patchTokens: string[] = [];
    await mockVersions(page);
    await openMockedEditor(page, { product });
    await page.route(
      (url) => url.pathname.endsWith("/activate"),
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...product, updatedAt: afterActivate }),
        }),
    );
    await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}`, async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON() as { expectedUpdatedAt?: string };
        patchTokens.push(body.expectedUpdatedAt ?? "");
      }
      return route.fallback();
    });

    await openMoreMenu(page);
    await page.getByRole("menuitem", { name: /Version history/i }).click();
    const legacy = page.getByTestId("product-version-row").filter({ hasText: "Legacy optimize title" });
    const activated = page.waitForResponse((response) => response.url().endsWith("/activate"));
    await legacy.getByTestId("version-activate").click();
    await activated;
    // The mutation's onSuccess has run once the button leaves its pending state.
    await expect(legacy.getByTestId("version-activate")).toBeEnabled();
    await page.keyboard.press("Escape");

    await page.getByLabel("Title").fill("Merchant edit after activating");
    await expect.poll(() => patchTokens.length, { timeout: 15_000 }).toBeGreaterThan(0);
    expect(patchTokens[0]).toBe(afterActivate);
  });

  test("closing the history sheet mid-activation still adopts the token", async ({ page }) => {
    // Regression for a gap found while writing these tests: the row's
    // per-call onSuccess never ran once the sheet unmounted.
    const product = buildSyntheticProduct();
    const afterActivate = new Date(Date.parse(product.updatedAt) + 11_000).toISOString();
    const patchTokens: string[] = [];
    await mockVersions(page);
    await openMockedEditor(page, { product });
    await page.route(
      (url) => url.pathname.endsWith("/activate"),
      async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 1_000));
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...product, updatedAt: afterActivate }),
        });
      },
    );
    await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}`, async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON() as { expectedUpdatedAt?: string };
        patchTokens.push(body.expectedUpdatedAt ?? "");
      }
      return route.fallback();
    });

    await openMoreMenu(page);
    await page.getByRole("menuitem", { name: /Version history/i }).click();
    const legacy = page.getByTestId("product-version-row").filter({ hasText: "Legacy optimize title" });
    const activated = page.waitForResponse((response) => response.url().endsWith("/activate"));
    await legacy.getByTestId("version-activate").click();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("product-version-row")).toHaveCount(0);
    await activated;
    await expect(page.getByTestId("editor-product-action-notice")).toHaveCount(0);

    await page.getByLabel("Title").fill("Merchant edit after the sheet closed");
    await expect.poll(() => patchTokens.length, { timeout: 15_000 }).toBeGreaterThan(0);
    expect(patchTokens[0]).toBe(afterActivate);
  });

  test("an edit made while Activate runs is not silently re-based", async ({ page }) => {
    const product = buildSyntheticProduct();
    const afterActivate = new Date(Date.parse(product.updatedAt) + 9_000).toISOString();
    const patchTokens: string[] = [];
    await mockVersions(page);
    // As the real server would: a save carrying the pre-activation token is
    // refused, which is what opens the review flow the notice promises.
    await openMockedEditor(page, { product, patchStatus: 409 });
    await page.route(
      (url) => url.pathname.endsWith("/activate"),
      async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 1_500));
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ ...product, updatedAt: afterActivate }),
        });
      },
    );
    await page.route(`**/api/v1/drafts/${DEMO_PRODUCT_ID}`, async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON() as { expectedUpdatedAt?: string };
        patchTokens.push(body.expectedUpdatedAt ?? "");
      }
      return route.fallback();
    });

    await openMoreMenu(page);
    await page.getByRole("menuitem", { name: /Version history/i }).click();
    const legacy = page.getByTestId("product-version-row").filter({ hasText: "Legacy optimize title" });
    const activated = page.waitForResponse((response) => response.url().endsWith("/activate"));
    await legacy.getByTestId("version-activate").click();
    await page.keyboard.press("Escape");
    // The merchant types while the activation is still in flight.
    await page.getByLabel("Title").fill("Typed during activation");
    await activated;

    // Not silently adopted: the merchant is told, and the save keeps the
    // token the edit was made against, so the server's 409 review applies.
    await expect(page.getByTestId("editor-product-action-notice")).toContainText(
      "updated while you were editing",
    );
    await expect.poll(() => patchTokens.length, { timeout: 15_000 }).toBeGreaterThan(0);
    expect(patchTokens[0]).toBe(product.updatedAt);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
  });
});

test.describe("I-2 — version history offers no invalid Activate", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("pipeline candidates have no Activate; legacy and original rows do", async ({ page }) => {
    await mockVersions(page);
    await openMockedEditor(page);
    await openMoreMenu(page);
    await page.getByRole("menuitem", { name: /Version history/i }).click();

    const rows = page.getByTestId("product-version-row");
    await expect(rows).toHaveCount(4, { timeout: 15_000 });

    const candidate = rows.filter({ hasText: "Pipeline candidate awaiting review" });
    await expect(candidate.getByTestId("version-activate")).toHaveCount(0);
    await expect(candidate.getByTestId("version-pipeline-candidate-note")).toBeVisible();
    await expect(candidate).toContainText("AI candidate");

    const legacy = rows.filter({ hasText: "Legacy optimize title" });
    await expect(legacy.getByTestId("version-activate")).toBeEnabled();
    const original = rows.filter({ hasText: "Version 1" });
    await expect(original.getByTestId("version-activate")).toBeEnabled();
  });
});
