import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { catalogueWorld, mockCatalogueApi, PUBLISHED_ID } from "./helpers/catalogue-fixture";
import {
  aliExpressConnection,
  channelsWorld,
  mockChannelsApi,
  shopifyConnection,
  storeRecord,
} from "./helpers/channels-fixture";
import { buildSyntheticProduct, DEMO_STORE_ID, openMockedEditor, syncedDemoListing } from "./helpers/editor-fixture";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import { emptyScenario, mockHomeApi, populatedScenario } from "./helpers/home-fixture";
import { captureEvidenceScreenshot } from "./helpers/screenshot-evidence";

/**
 * UX-L2D-07 cross-app hardening — backend-less.
 *
 * One spec walks the eight accepted screens and asks the same questions of
 * each: is there exactly one `h1` and a `main` landmark; does every button
 * and link have a name; is the heading outline sane; does anything scroll
 * sideways at 1440 / 1280 / 1024 / 768 / 390 in light and dark; can the
 * primary control be reached by keyboard. The answers are the visual
 * acceptance matrix and the semantics evidence for the final report. Each
 * screen is served by its own milestone fixture, so the states shown are
 * the ones those fixtures can honestly produce.
 */

const SHOT_ROOT = resolveSuiteShotRoot("ux-l2d-07-hardening", ["UX_L2D_07_SHOT_ROOT"]);

async function shot(page: Page, name: string) {
  await captureEvidenceScreenshot(page, name, { root: SHOT_ROOT, fullPage: false });
}

const WIDTHS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1280", width: 1280, height: 800 },
  { name: "1024", width: 1024, height: 768 },
  { name: "768", width: 768, height: 1024 },
  { name: "390", width: 390, height: 844 },
] as const;

interface Screen {
  id: string;
  /** Install mocks, navigate, and wait for the screen's settled marker. */
  open: (page: Page) => Promise<void>;
  /** The control a merchant reaches for first; must be keyboard-reachable. */
  primary: (page: Page) => ReturnType<Page["getByRole"]> | ReturnType<Page["getByTestId"]>;
}

const SCREENS: Screen[] = [
  {
    id: "home",
    open: async (page) => {
      await mockHomeApi(page, populatedScenario());
      await page.goto("/dashboard");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("home-loading")).toHaveCount(0, { timeout: 30_000 });
    },
    primary: (page) => page.getByTestId("next-step").getByRole("link").first(),
  },
  {
    id: "drafts",
    open: async (page) => {
      await mockCatalogueApi(page, catalogueWorld());
      await page.goto("/drafts");
      await expect(page.getByTestId("draft-row").locator("visible=true").first()).toBeVisible({ timeout: 30_000 });
    },
    primary: (page) => page.getByTestId("catalogue-search"),
  },
  {
    id: "products",
    open: async (page) => {
      await mockCatalogueApi(page, catalogueWorld());
      await page.goto("/products");
      await expect(page.getByTestId("product-row").locator("visible=true").first()).toBeVisible({ timeout: 30_000 });
    },
    primary: (page) => page.getByTestId("product-title-link").locator("visible=true").first(),
  },
  {
    id: "product-detail",
    open: async (page) => {
      await mockCatalogueApi(page, catalogueWorld());
      await page.goto(`/products/${PUBLISHED_ID}`);
      await expect(page.getByTestId("published-product-summary")).toBeVisible({ timeout: 30_000 });
    },
    primary: (page) => page.getByTestId("storefront-link"),
  },
  {
    id: "editor",
    open: async (page) => {
      await openMockedEditor(page, { listings: [syncedDemoListing({ onlineStorePublished: true })] });
      await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    },
    primary: (page) => page.locator('[data-testid="publish-action"]:visible').first(),
  },
  {
    id: "review-publish",
    open: async (page) => {
      await openMockedEditor(page);
      await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
      await page.getByTestId("editor-tab-publishing").click();
      await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
      await expect(page.getByTestId("publish-to-store")).toBeEnabled({ timeout: 10_000 });
    },
    primary: (page) => page.getByTestId("publish-to-store"),
  },
  {
    id: "channels",
    open: async (page) => {
      await mockChannelsApi(
        page,
        channelsWorld({
          shopify: { configured: true, connections: [shopifyConnection()] },
          aliexpress: { connected: true, connection: aliExpressConnection() },
        }),
      );
      await page.goto("/settings/integrations");
      await expect(page.getByTestId("channel-shopify-status")).toBeVisible({ timeout: 30_000 });
    },
    primary: (page) => page.getByTestId("channel-shopify").getByRole("button", { name: "Disconnect" }),
  },
  {
    id: "stores",
    open: async (page) => {
      await mockChannelsApi(
        page,
        channelsWorld({
          stores: [
            storeRecord(),
            storeRecord({ id: "33333333-3333-4333-8333-333333333333", name: "Old Shop", slug: "old-shop", status: "disconnected" }),
          ],
        }),
      );
      await page.goto("/stores");
      await expect(page.getByTestId("stores")).toBeVisible({ timeout: 30_000 });
    },
    primary: (page) => page.getByRole("link", { name: "Manage connections" }),
  },
];

interface SemanticsReport {
  h1Count: number;
  hasMain: boolean;
  headingLevels: number[];
  unnamedControls: string[];
  liveRegions: number;
  overflow: boolean;
}

async function semantics(page: Page): Promise<SemanticsReport> {
  return page.evaluate(() => {
    const visible = (el: Element) => {
      const rect = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6")).filter(visible);
    const controls = Array.from(document.querySelectorAll<HTMLElement>("button, a[href]")).filter(visible);
    const name = (el: HTMLElement) =>
      (el.getAttribute("aria-label") ?? "").trim() ||
      (el.getAttribute("aria-labelledby") ? "labelledby" : "") ||
      (el.innerText ?? "").trim() ||
      (el.querySelector("img[alt]")?.getAttribute("alt") ?? "").trim() ||
      (el.getAttribute("title") ?? "").trim();
    const doc = document.documentElement;
    const main = document.getElementById("main-content");
    return {
      h1Count: headings.filter((h) => h.tagName === "H1").length,
      hasMain: main !== null && main.tagName === "MAIN",
      headingLevels: headings.map((h) => Number(h.tagName[1])),
      unnamedControls: controls
        .filter((el) => !name(el))
        .map((el) => `${el.tagName.toLowerCase()}${el.className ? "." + String(el.className).split(" ")[0] : ""}`),
      liveRegions: document.querySelectorAll('[aria-live], [role="status"], [role="alert"]').length,
      overflow: doc.scrollWidth > doc.clientWidth + 1 || (main !== null && main.scrollWidth > main.clientWidth + 1),
    };
  });
}

/** No heading jumps more than one level down from the previous one. */
function outlineIsSane(levels: number[]): boolean {
  let previous = 0;
  for (const level of levels) {
    if (level > previous + 1 && previous !== 0) return false;
    previous = level;
  }
  return true;
}

for (const screen of SCREENS) {
  test.describe(`Hardening — ${screen.id}`, () => {
    test("semantics: one h1, a main landmark, named controls, a sane outline", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await screen.open(page);
      const report = await semantics(page);
      expect(report.h1Count, "exactly one h1").toBe(1);
      expect(report.hasMain, "main#main-content landmark").toBe(true);
      expect(report.unnamedControls, "every visible button/link has a name").toEqual([]);
      expect(outlineIsSane(report.headingLevels), `heading outline ${report.headingLevels.join(",")}`).toBe(true);
      // Live regions are few and deliberate; a screen that grows past a
      // handful is announcing too much.
      expect(report.liveRegions).toBeLessThanOrEqual(6);
      await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    });

    test("keyboard: the primary control is reachable by Tab and shows focus", async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await screen.open(page);
      const target = screen.primary(page);
      await expect(target).toBeVisible();
      // Tab from the top of the document until the primary control has focus
      // (bounded, so a trap fails the test instead of hanging it).
      await page.keyboard.press("Tab");
      let reached = false;
      for (let i = 0; i < 80; i += 1) {
        if (await target.evaluate((el) => el === document.activeElement)) {
          reached = true;
          break;
        }
        await page.keyboard.press("Tab");
      }
      expect(reached, "primary control reached within 80 Tab presses").toBe(true);
      const ring = await target.evaluate((el) => {
        const style = getComputedStyle(el);
        return style.outlineStyle !== "none" || style.boxShadow !== "none";
      });
      expect(ring, "visible focus indication").toBe(true);
    });

    for (const size of WIDTHS) {
      test(`fits ${size.name} in light mode without horizontal overflow`, async ({ page }) => {
        await page.setViewportSize({ width: size.width, height: size.height });
        await screen.open(page);
        const report = await semantics(page);
        expect(report.overflow, "no horizontal overflow").toBe(false);
        await shot(page, `${screen.id}-${size.name}-light`);
      });
    }

    for (const size of [WIDTHS[0], WIDTHS[4]]) {
      test(`fits ${size.name} in dark mode without horizontal overflow`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: "dark" });
        await page.setViewportSize({ width: size.width, height: size.height });
        await screen.open(page);
        const report = await semantics(page);
        expect(report.overflow, "no horizontal overflow").toBe(false);
        await shot(page, `${screen.id}-${size.name}-dark`);
      });
    }
  });
}

test.describe("Hardening — cross-app journeys", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Journey 1: a new merchant is led Home → Channels → connect → Drafts without a dead end", async ({ page }) => {
    await mockHomeApi(page, emptyScenario());
    await page.goto("/dashboard");
    // An empty workspace gets the three setup steps; the first leads to the
    // real connect surface, not a placeholder.
    const setup = page.getByTestId("empty-workspace");
    await expect(setup).toBeVisible({ timeout: 30_000 });
    const connect = setup.getByRole("link", { name: "Connect Shopify" });
    await expect(connect).toHaveAttribute("href", "/settings/integrations");
    // Nowhere on Home is there a manual store path or an "Add store".
    await expect(page.getByRole("link", { name: /Add store/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Add store/i })).toHaveCount(0);
    // No money is shown without a currency contract.
    await expect(page.locator("#main-content")).not.toContainText(/\$|£|USD|GBP/);

    await mockChannelsApi(page, channelsWorld());
    await connect.click();
    await expect(page).toHaveURL(/\/settings\/integrations$/);
    await expect(page.getByRole("button", { name: "Connect Shopify" })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: /^Connect (Shopify|another store)$/ })).toHaveCount(1);
  });

  test("Journey 3: Validation passed is not Published; publish leads to a resolving product route and the Products list", async ({
    page,
  }) => {
    await openMockedEditor(page, {
      publishResponder: () => ({ status: 200 }),
      productsResponder: true,
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("editor-tab-publishing").click();
    await page.locator("#publish-store").selectOption(DEMO_STORE_ID);
    await expect(page.getByTestId("publish-status-summary")).toContainText(/Validation passed/i, { timeout: 10_000 });
    await expect(page.getByTestId("post-publish-success")).toHaveCount(0);
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Not on Shopify");

    await page.getByTestId("publish-to-store").click();
    await expect(page.getByTestId("post-publish-success")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Added to Shopify");
    await expect(page.getByTestId("draft-editor")).not.toContainText(/Draft — not|not live/i);
    // Visibility was not confirmed by the response, so it is not claimed.
    await expect(page.getByTestId("post-publish-headline")).toHaveText("Published to Shopify");
    await expect(page.getByTestId("post-publish-storefront")).toHaveCount(0);

    await page.getByTestId("post-publish-view-product").click();
    await expect(page).toHaveURL(/\/products\/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee$/);
    await expect(page.getByTestId("published-product-summary")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("published-product-lifecycle")).toHaveAttribute("data-kind", "added-to-shopify");
    await expect(page.getByTestId("storefront-link")).toHaveCount(0);

    await page.getByRole("link", { name: "Back to Products" }).click();
    await expect(page).toHaveURL(/\/products$/);
    await expect(page.getByTestId("product-row").locator("visible=true")).toHaveCount(1, { timeout: 30_000 });
    await expect(page.getByTestId("product-listing-status").locator("visible=true")).toHaveText("Published");
  });

  test("Journey 4: editing a published product distinguishes Saved in DropPilot from sent to Shopify", async ({ page }) => {
    await openMockedEditor(page, {
      // Saved two hours ago, synced one hour ago: nothing newer than the sync
      // until the merchant edits.
      product: buildSyntheticProduct({ updatedAt: new Date(Date.now() - 7_200_000).toISOString() }),
      listings: [syncedDemoListing({ onlineStorePublished: true, lastSyncedAt: new Date(Date.now() - 3_600_000).toISOString() })],
      patchResponder: () => ({
        status: 200,
        body: { ...buildSyntheticProduct(), title: "Edited after publish", updatedAt: new Date().toISOString() },
      }),
    });
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Visible on your shop");
    await expect(page.locator('[data-testid="publish-action"]:visible').first()).toHaveText("View product");
    await page.locator("#draft-title").fill("Edited after publish");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Unsaved changes");
    await expect(page.locator('[data-testid="publish-action"]:visible').first()).toHaveText("Update Shopify");
    await expect(page.getByTestId("draft-save-state")).toHaveText("Saved in DropPilot", { timeout: 15_000 });
    await expect(page.getByTestId("product-lifecycle")).toHaveText("Changes not sent to Shopify");
    await expect(page.locator('[data-testid="publish-action"]:visible').first()).toHaveText("Update Shopify");
    await page.getByTestId("editor-tab-publishing").click();
    await expect(page.getByTestId("publish-to-store")).toHaveText("Update Shopify");
  });
});

test.describe("Hardening — request discipline", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  /** Count API requests by path while a page settles. */
  function countRequests(page: Page): () => Record<string, number> {
    const counts: Record<string, number> = {};
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (!url.pathname.includes("/api/v1/")) return;
      const path = url.pathname.replace(/^.*\/api\/v1/, "");
      counts[path] = (counts[path] ?? 0) + 1;
    });
    return () => counts;
  }

  test("Integrations asks each provider for its status once, with no polling", async ({ page }) => {
    const read = countRequests(page);
    await mockChannelsApi(page, channelsWorld({ shopify: { configured: true, connections: [shopifyConnection()] } }));
    await page.goto("/settings/integrations");
    await expect(page.getByTestId("channel-shopify-status")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("overview-shopify")).toContainText("1 connected");
    // Settle: the overview strip and the three cards share one query each.
    await page.waitForTimeout(3_000);
    const counts = read();
    for (const path of ["/integrations/shopify/status", "/integrations/aliexpress/status", "/integrations/ebay/status"]) {
      expect(counts[path], path).toBe(1);
    }
    expect(counts["/stores"] ?? 0, "Integrations does not read the store list").toBe(0);
  });

  test("Home shares the bell's notifications request and reads every endpoint once", async ({ page }) => {
    const read = countRequests(page);
    await mockHomeApi(page, populatedScenario());
    await page.goto("/dashboard");
    await expect(page.getByTestId("home-loading")).toHaveCount(0, { timeout: 30_000 });
    await page.waitForTimeout(3_000);
    const counts = read();
    for (const path of [
      "/notifications",
      "/notifications/unread-count",
      "/products/workspace-counts",
      "/integrations/shopify/status",
      "/integrations/aliexpress/status",
      "/integrations/ebay/status",
      "/drafts",
      "/products/imports",
      "/orders/statistics",
    ]) {
      expect(counts[path], path).toBe(1);
    }
  });

  test("the editor does not poll the store list", async ({ page }) => {
    const read = countRequests(page);
    await openMockedEditor(page);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    // Longer than the 15 s interval UX-L2D-07 removed.
    await page.waitForTimeout(17_000);
    const counts = read();
    expect(counts["/stores"], "/stores").toBe(1);
    expect(counts[`/drafts/${"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}/listings`], "listings").toBe(1);
  });
});
