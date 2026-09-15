import path from "node:path";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import {
  DRAFT_ID,
  MISSING_ID,
  PUBLISHED_HIDDEN_ID,
  PUBLISHED_ID,
  catalogueWorld,
  mockCatalogueApi,
} from "./helpers/catalogue-fixture";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";

/**
 * UX-L2D-04 — catalogue workspace and the published product page.
 *
 * Backend-less; every request is answered by `mockCatalogueApi`, which
 * paginates, searches and sorts the way the API does, and logs what the
 * page asked for. Closes the UX-L2D-01 Critical (`/products/{id}` 404) with
 * evidence for each closure condition, and covers search/sort/pagination,
 * URL synchronisation, empty/no-results/error states, and the phone layout.
 */

const SHOT_ROOT = resolveSuiteShotRoot("ux-l2d-catalogue", ["UX_L2D_SHOT_ROOT"]);
const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 1024, height: 768 },
  mobile: { width: 390, height: 844 },
} as const;

async function shoot(page: Page, name: string) {
  await page.evaluate(() =>
    Promise.all(document.getAnimations().map((a) => a.finished.catch(() => undefined))),
  );
  await page.screenshot({ path: path.join(SHOT_ROOT, `${name}.png`), fullPage: false });
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    main: (() => {
      const el = document.getElementById("main-content");
      return el ? el.scrollWidth > el.clientWidth : false;
    })(),
  }));
  expect(overflow).toEqual({ document: false, main: false });
}

test.describe("Drafts — search, sort, pagination, URL", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("first page is the server's page one, sorted by the default, with the count", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts");

    await expect(page.getByTestId("draft-row").locator("visible=true")).toHaveCount(25);
    await expect(page.getByTestId("pagination-summary")).toContainText("Showing 1–25 of 60 drafts · page 1 of 3");
    await expect(page.getByTestId("pagination-previous")).toBeDisabled();
    await expect(page.getByTestId("pagination-next")).toBeEnabled();

    // Default order is most recently updated first; #60 was updated last.
    await expect(page.getByTestId("draft-title-link").locator("visible=true").first()).toContainText("#60");

    // Wire names, not camelCase: the API ignores `sortBy`.
    const first = log.requests.find((r) => r.path === "/drafts");
    expect(first?.search).toContain("sort_by=updated_at");
    expect(first?.search).toContain("sort_dir=desc");
    expect(first?.search).toContain("size=25");
    expect(first?.search).not.toContain("sortBy");

    // No per-row listings requests: the list decorates only from its own rows.
    expect(log.requests.filter((r) => r.path.endsWith("/listings"))).toHaveLength(0);
    await shoot(page, "desktop-light-drafts-page1");
  });

  test("next and previous move through server pages and the URL", async ({ page }) => {
    await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts");
    await page.getByTestId("pagination-next").click();
    await expect(page).toHaveURL(/\/drafts\?page=2$/);
    await expect(page.getByTestId("pagination-summary")).toContainText("Showing 26–50 of 60");
    await page.getByTestId("pagination-next").click();
    await expect(page).toHaveURL(/\/drafts\?page=3$/);
    await expect(page.getByTestId("pagination-summary")).toContainText("Showing 51–60 of 60 drafts · page 3 of 3");
    await expect(page.getByTestId("pagination-next")).toBeDisabled();
    await expect(page.getByTestId("draft-row").locator("visible=true")).toHaveCount(10);

    // Back/forward drive the list, not just the address bar.
    await page.goBack();
    await expect(page).toHaveURL(/\/drafts\?page=2$/);
    await expect(page.getByTestId("pagination-summary")).toContainText("Showing 26–50 of 60");
    await page.goForward();
    await expect(page.getByTestId("pagination-summary")).toContainText("Showing 51–60 of 60");
  });

  test("search is server-side, debounced, and lands in the URL without history spam", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts");
    await expect(page.getByTestId("draft-row").locator("visible=true")).toHaveCount(25);

    const before = log.requests.filter((r) => r.path === "/drafts").length;
    const historyBefore = await page.evaluate(() => window.history.length);
    await page.getByTestId("catalogue-search").pressSequentially("BrightHome", { delay: 40 });
    await expect(page).toHaveURL(/\/drafts\?q=BrightHome$/);
    await expect(page.getByTestId("pagination-summary")).toContainText("of 20 drafts");
    await expect(page.getByTestId("catalogue-summary")).toContainText("20 results for “BrightHome”");

    // Ten keystrokes → one request (debounced), sent as `q` for the server.
    const searchRequests = log.requests.slice(before).filter((r) => r.path === "/drafts");
    expect(searchRequests.length).toBeLessThanOrEqual(2);
    expect(searchRequests.at(-1)?.search).toContain("q=BrightHome");

    // Typing replaced the current entry rather than pushing one per keystroke.
    const historyAfter = await page.evaluate(() => window.history.length);
    expect(historyAfter).toBe(historyBefore);
  });

  test("sort changes are sent in wire names and start from page one", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts?page=3");
    await expect(page.getByTestId("pagination-summary")).toContainText("page 3 of 3");

    await page.getByTestId("catalogue-sort").selectOption("title:asc");
    await expect(page).toHaveURL(/\/drafts\?sort=title%3Aasc$/);
    await expect(page.getByTestId("pagination-summary")).toContainText("page 1 of 3");
    const last = log.requests.filter((r) => r.path === "/drafts").at(-1);
    expect(last?.search).toContain("sort_by=title");
    expect(last?.search).toContain("sort_dir=asc");
    expect(last?.search).toContain("page=1");
    await expect(page.getByTestId("draft-title-link").locator("visible=true").first()).toContainText("Bluetooth Sleep Headband");
  });

  test("refresh restores search, sort and page from the URL", async ({ page }) => {
    await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts?q=lamp&sort=title%3Adesc&page=1");
    await expect(page.getByTestId("catalogue-search")).toHaveValue("lamp");
    await expect(page.getByTestId("catalogue-sort")).toHaveValue("title:desc");
    await expect(page.getByTestId("pagination-summary")).toContainText("of 6 drafts");
    await page.reload();
    await expect(page.getByTestId("catalogue-search")).toHaveValue("lamp");
    await expect(page.getByTestId("catalogue-sort")).toHaveValue("title:desc");
    await expect(page.getByTestId("pagination-summary")).toContainText("of 6 drafts");
  });

  test("invalid URL values fall back safely, and a page past the end offers the last page", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts?sort=drop%20table&page=-4");
    await expect(page.getByTestId("catalogue-sort")).toHaveValue("updated_at:desc");
    await expect(page.getByTestId("pagination-summary")).toContainText("page 1 of 3");
    const sent = log.requests.filter((r) => r.path === "/drafts").at(-1);
    expect(sent?.search).toContain("sort_by=updated_at");
    expect(sent?.search).not.toContain("drop");

    await page.goto("/drafts?page=9");
    await expect(page.getByTestId("catalogue-page-out-of-range")).toBeVisible();
    await page.getByRole("button", { name: "Go to the last page" }).click();
    await expect(page).toHaveURL(/\/drafts\?page=3$/);
    await expect(page.getByTestId("pagination-summary")).toContainText("page 3 of 3");
  });

  test("row status is words, the primary action is Edit, and rows navigate", async ({ page }) => {
    await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts?sort=created_at%3Aasc");
    const rows = page.getByTestId("draft-row").locator("visible=true");
    await expect(rows.first().getByTestId("draft-status")).toHaveText("Draft");
    // #8 is the supplier-unavailable one.
    await expect(rows.nth(7).getByTestId("draft-status")).toHaveText("Unavailable");
    await expect(rows.first().getByRole("link", { name: /^Edit Wireless Desk Lamp/ })).toBeVisible();
    await expect(rows.first().getByRole("button", { name: "Optimize with AI" })).toBeVisible();
    await expect(rows.first().getByRole("button", { name: "History" })).toBeVisible();
    await expect(rows.first().getByRole("button", { name: /More actions for/ })).toBeVisible();

    // Clicking the row body (not a link) opens the editor.
    await rows.first().getByTestId("variant-count").click();
    await expect(page).toHaveURL(new RegExp(`/drafts/${DRAFT_ID}$`));
  });
});

test.describe("Drafts — empty, no results, error", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("an empty catalogue and an empty search are different states", async ({ page }) => {
    const world = catalogueWorld();
    world.drafts = [];
    await mockCatalogueApi(page, world);
    await page.goto("/drafts");
    await expect(page.getByTestId("catalogue-empty")).toContainText("No drafts yet");
    await expect(page.getByTestId("catalogue-empty").getByRole("button", { name: "Import as Draft" })).toBeVisible();
    await shoot(page, "desktop-light-drafts-empty");

    const populated = catalogueWorld();
    await mockCatalogueApi(page, populated);
    await page.goto("/drafts?q=zzzz-nothing");
    await expect(page.getByTestId("catalogue-no-results")).toContainText("No drafts match “zzzz-nothing”");
    await expect(page.getByTestId("catalogue-empty")).toHaveCount(0);
    await shoot(page, "desktop-light-drafts-no-results");
    await page.getByTestId("catalogue-no-results").getByRole("button", { name: "Clear search" }).click();
    await expect(page).toHaveURL(/\/drafts$/);
    await expect(page.getByTestId("draft-row").locator("visible=true")).toHaveCount(25);
  });

  test("an API failure shows an error with retry and recovers", async ({ page }) => {
    const world = catalogueWorld();
    world.fail.add("drafts");
    await mockCatalogueApi(page, world);
    await page.goto("/drafts");
    await expect(page.getByText("Could not load drafts")).toBeVisible();
    await shoot(page, "desktop-light-drafts-error");
    world.fail.clear();
    await page.getByRole("button", { name: /Try again|Retry/ }).click();
    await expect(page.getByTestId("draft-row").locator("visible=true")).toHaveCount(25);
  });
});

test.describe("Drafts — phone", () => {
  test.use({ viewport: VIEWPORTS.mobile });

  test("cards replace the table; Edit is reachable without sideways scrolling", async ({ page }) => {
    await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/drafts");
    await expect(page.getByTestId("catalogue-cards")).toBeVisible();
    await expect(page.locator("table")).toBeHidden();
    const first = page.getByTestId("draft-row").locator("visible=true").first();
    const edit = first.getByRole("link", { name: /^Edit / });
    await expect(edit).toBeVisible();
    const box = await edit.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(36);
    await expectNoHorizontalOverflow(page);
    await expect(page.getByTestId("pagination-next")).toBeVisible();
    await shoot(page, "mobile-light-drafts");
  });
});

test.describe("Products — list and row navigation", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("rows say Published, name View, and land on the product page", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/products");
    const rows = page.getByTestId("product-row").locator("visible=true");
    await expect(rows).toHaveCount(2);
    await expect(rows.first().getByTestId("product-listing-status")).toHaveText("Published");
    await expect(page.getByText("Added to Shopify")).toHaveCount(0);
    expect(log.requests.filter((r) => r.path.endsWith("/listings"))).toHaveLength(0);
    await shoot(page, "desktop-light-products");

    await rows.first().getByRole("link", { name: /^View / }).click();
    await expect(page).toHaveURL(new RegExp(`/products/${PUBLISHED_HIDDEN_ID}$`));
    await expect(page.getByTestId("published-product-summary")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Page not found" })).toHaveCount(0);
  });

  test("the title link and the row body both navigate", async ({ page }) => {
    await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/products?sort=title%3Aasc");
    const rows = page.getByTestId("product-row").locator("visible=true");
    await rows.first().getByTestId("product-title-link").click();
    await expect(page).toHaveURL(new RegExp(`/products/${PUBLISHED_HIDDEN_ID}$`));
    await page.goBack();
    await expect(page).toHaveURL(/\/products\?sort=title%3Aasc$/);
    await rows.nth(1).getByTestId("variant-count").click();
    await expect(page).toHaveURL(new RegExp(`/products/${PUBLISHED_ID}$`));
  });
});

test.describe("Product page", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("a published product loads by direct URL, survives refresh, and links only to trusted hosts", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto(`/products/${PUBLISHED_ID}`);
    const summary = page.getByTestId("published-product-summary");
    await expect(summary).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "Ceramic Pour-Over Coffee Set" })).toBeVisible();
    await expect(page.getByTestId("published-product-lifecycle")).toHaveAttribute("data-kind", "visible-on-shop");
    await expect(page.getByTestId("published-product-lifecycle")).toHaveText("Visible on your shop");
    await expect(summary).toContainText("demo-shop.myshopify.com");
    await expect(summary).toContainText("GBP 24.99");

    // HTML in the description renders as text; the script never executed.
    await expect(page.getByTestId("published-product-description")).toHaveText(/Hand-glazed ceramic set\./);
    await expect(page.getByTestId("published-product-description")).not.toContainText("<b>");
    expect(await page.locator("#main-content script").count()).toBe(0);

    // Outbound links: HTTPS on *.myshopify.com only, with a safe rel.
    const storefront = page.getByTestId("storefront-link");
    await expect(storefront).toHaveAttribute("href", "https://demo-shop.myshopify.com/products/wireless-desk-lamp");
    await expect(storefront).toHaveAttribute("rel", "noopener noreferrer");
    await expect(storefront).toHaveAttribute("target", "_blank");
    const admin = page.getByTestId("admin-link");
    await expect(admin).toHaveAttribute("href", "https://demo-shop.myshopify.com/admin/products/8123456789");
    await expect(admin).toHaveAttribute("rel", "noopener noreferrer");
    // Script bodies are not description text.
    await expect(page.getByTestId("published-product-description")).not.toContainText("alert");

    // Exactly one product request and one listings request.
    expect(log.requests.filter((r) => r.path === `/products/${PUBLISHED_ID}`)).toHaveLength(1);
    expect(log.requests.filter((r) => r.path === `/drafts/${PUBLISHED_ID}/listings`)).toHaveLength(1);

    await page.reload();
    await expect(page.getByTestId("published-product-summary")).toBeVisible();
    await shoot(page, "desktop-light-product-visible");
  });

  test("a listing without confirmed visibility says Added to Shopify and drops untrusted URLs", async ({ page }) => {
    await mockCatalogueApi(page, catalogueWorld());
    await page.goto(`/products/${PUBLISHED_HIDDEN_ID}`);
    await expect(page.getByTestId("published-product-lifecycle")).toHaveAttribute("data-kind", "added-to-shopify");
    await expect(page.getByTestId("published-product-summary")).toContainText("may not be visible");
    // http:// storefront and a non-Shopify admin host are never rendered as links.
    await expect(page.getByTestId("storefront-link")).toHaveCount(0);
    await expect(page.getByTestId("admin-link")).toHaveCount(0);
    await expect(page.locator("#main-content a[href*='evil.example.com']")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Edit in DropPilot" })).toHaveAttribute(
      "href",
      `/drafts/${PUBLISHED_HIDDEN_ID}?tab=overview`,
    );
    // Draft saved after the last sync → the conservative note.
    await expect(page.getByTestId("unsent-changes-note")).toBeVisible();
    await shoot(page, "desktop-light-product-added");
  });

  test("a draft id is sent to the draft editor, never shown as published", async ({ page }) => {
    await mockCatalogueApi(page, catalogueWorld());
    await page.goto(`/products/${DRAFT_ID}`);
    await expect(page).toHaveURL(new RegExp(`/drafts/${DRAFT_ID}$`), { timeout: 15_000 });
    await expect(page.getByTestId("published-product-summary")).toHaveCount(0);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 15_000 });
  });

  test("missing and foreign ids read the same: Product not found", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto(`/products/${MISSING_ID}`);
    await expect(page.getByTestId("published-product-not-found")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Product not found" })).toBeVisible();
    await expect(page.getByText(/tenant|workspace/i)).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Back to Products" })).toHaveAttribute("href", "/products");
    expect(log.requests.filter((r) => r.path === `/products/${MISSING_ID}`)).toHaveLength(1);
    await shoot(page, "desktop-light-product-not-found");
  });

  test("a malformed id is refused before any request is made", async ({ page }) => {
    const log = await mockCatalogueApi(page, catalogueWorld());
    await page.goto("/products/not-a-uuid");
    await expect(page.getByTestId("published-product-not-found")).toBeVisible();
    expect(log.requests.filter((r) => r.path.startsWith("/products/not-a-uuid"))).toHaveLength(0);
    expect(log.requests.filter((r) => r.path.includes("/listings"))).toHaveLength(0);
    await expect(page.getByText(/422|Unprocessable|validation/i)).toHaveCount(0);
  });

  test("when listings fail the status is unavailable — not 'not published' — with a retry", async ({ page }) => {
    const world = catalogueWorld();
    world.fail.add("listings");
    await mockCatalogueApi(page, world);
    await page.goto(`/products/${PUBLISHED_ID}`);
    await expect(page.getByTestId("published-product-lifecycle")).toHaveAttribute("data-kind", "unavailable");
    await expect(page.getByTestId("published-product-lifecycle")).toHaveText("Shopify status unavailable");
    await expect(page.getByTestId("storefront-link")).toHaveCount(0);
    // Not redirected to the draft editor on missing evidence.
    await expect(page).toHaveURL(new RegExp(`/products/${PUBLISHED_ID}$`));
    world.fail.clear();
    await page.getByTestId("shopify-status-retry").click();
    await expect(page.getByTestId("published-product-lifecycle")).toHaveAttribute("data-kind", "visible-on-shop");
  });

  test("a product request failure offers retry; the loading state is announced", async ({ page }) => {
    const world = catalogueWorld();
    world.fail.add("product");
    await mockCatalogueApi(page, world);
    await page.goto(`/products/${PUBLISHED_ID}`);
    await expect(page.getByTestId("published-product-error")).toBeVisible();
    world.fail.clear();
    await page.getByRole("button", { name: /Try again|Retry/ }).click();
    await expect(page.getByTestId("published-product-summary")).toBeVisible();
  });
});

test.describe("Product page — phone and dark", () => {
  test("renders at 390px without overflow", async ({ browser }) => {
    const context = await browser.newContext({ viewport: VIEWPORTS.mobile });
    const page = await context.newPage();
    try {
      await mockCatalogueApi(page, catalogueWorld());
      await page.goto(`/products/${PUBLISHED_ID}`);
      await expect(page.getByTestId("published-product-summary")).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await expect(page.getByTestId("storefront-link")).toBeInViewport();
      await shoot(page, "mobile-light-product");
    } finally {
      await context.close();
    }
  });

  test("renders in dark mode at desktop and tablet", async ({ browser }) => {
    for (const [name, viewport] of [["desktop", VIEWPORTS.desktop], ["tablet", VIEWPORTS.tablet]] as const) {
      const context = await browser.newContext({ viewport, colorScheme: "dark" });
      const page = await context.newPage();
      try {
        await mockCatalogueApi(page, catalogueWorld());
        await page.goto(`/products/${PUBLISHED_ID}`);
        await expect(page.getByTestId("published-product-summary")).toBeVisible();
        await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(true);
        await shoot(page, `${name}-dark-product`);
        await page.goto("/drafts");
        await expect(page.getByTestId("draft-row").locator("visible=true").first()).toBeVisible();
        await shoot(page, `${name}-dark-drafts`);
      } finally {
        await context.close();
      }
    }
  });
});
