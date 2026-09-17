import path from "node:path";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import {
  CONNECTED_ALIEXPRESS,
  CONNECTED_SHOPIFY,
  HOME_DRAFT_IDS,
  NO_ALIEXPRESS,
  NO_SHOPIFY,
  attentionScenario,
  emptyScenario,
  mockHomeApi,
  populatedScenario,
} from "./helpers/home-fixture";

/**
 * UX-L2D-03 — Home.
 *
 * Backend-less: every request is answered from a scenario, so each state a
 * merchant can meet is rendered deterministically — populated, empty,
 * several things wrong, one block failing — and the rules in
 * `components/home/home-rules.ts` are proved through the screen, not by
 * reading them. Maps to UX-L2D-01 findings F-2 (analytics wall, no actions,
 * zero-wall empty state) and F-6 (hard-coded USD).
 */

const SHOT_ROOT = resolveSuiteShotRoot("ux-l2d-home", ["UX_L2D_SHOT_ROOT"]);

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 1024, height: 768 },
  mobile: { width: 390, height: 844 },
} as const;

async function shoot(page: Page, name: string) {
  await page.evaluate(() =>
    Promise.all(document.getAnimations().map((a) => a.finished.catch(() => undefined))),
  );
  await page.screenshot({ path: path.join(SHOT_ROOT, `${name}.png`), fullPage: true });
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

async function expectNoMoneyOnHome(page: Page) {
  // F-6: Home used to label revenue "USD" for every workspace. It now shows
  // no monetary figure at all, and none of the analytics blocks.
  const main = page.locator("#main-content");
  await expect(main.getByText(/\bUSD\b/)).toHaveCount(0);
  await expect(main.getByText(/\bRevenue\b/)).toHaveCount(0);
  await expect(main.getByRole("heading", { name: "Sales overview" })).toHaveCount(0);
  await expect(main.getByRole("heading", { name: "Top products" })).toHaveCount(0);
  await expect(main.getByRole("region", { name: "Key metrics" })).toHaveCount(0);
}

test.describe("Home — populated workspace", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("answers the five merchant questions from real endpoints", async ({ page }) => {
    const log = await mockHomeApi(page, populatedScenario());
    await page.goto("/dashboard");

    // Heading hierarchy: one h1, then one h2 per block.
    await expect(page.getByRole("heading", { level: 1, name: /Welcome back/ })).toBeVisible();
    // Scoped to `main`: the sidebar has an h2 "Channels" of its own.
    const main = page.locator("#main-content");
    for (const name of ["Needs attention", "Continue working", "Your catalogue", "Channels", "Recent activity"]) {
      await expect(main.getByRole("heading", { level: 2, name })).toBeVisible();
    }

    // Nothing wrong → calm line, not an empty error panel.
    await expect(page.getByTestId("attention-clear")).toContainText("Nothing needs your attention");

    // Next step: both channels connected and drafts exist → continue the latest.
    const next = page.getByTestId("next-step");
    await expect(next).toHaveAttribute("data-rule", "continue-draft");
    await expect(next.getByRole("link", { name: "Open draft" })).toHaveAttribute(
      "href",
      `/drafts/${HOME_DRAFT_IDS[0]}`,
    );

    // Counts come from workspace-counts, same source as the sidebar badges.
    await expect(page.getByTestId("summary-drafts")).toContainText("7");
    await expect(page.getByTestId("summary-products")).toContainText("2");
    await expect(page.getByTestId("summary-orders")).toContainText("3");
    await expect(page.getByTestId("nav-badge-drafts").first()).toHaveText("7");

    // Recent drafts, most recently edited first, each with a named Edit link.
    const drafts = page.getByTestId("recent-drafts");
    await expect(drafts.getByRole("listitem")).toHaveCount(3);
    await expect(drafts.getByRole("listitem").first()).toContainText("Wireless Desk Lamp");
    await expect(
      drafts.getByRole("link", { name: "Edit Wireless Desk Lamp with USB Charging" }),
    ).toHaveAttribute("href", `/drafts/${HOME_DRAFT_IDS[0]}`);
    // The list asked the API for updated_at desc in the wire names it reads.
    expect(log.drafts.some((q) => q.includes("sort_by=updated_at") && q.includes("sort_dir=desc"))).toBe(true);

    // Channels: word + sentence, never a bare colour.
    await expect(page.getByTestId("channel-shopify")).toHaveAttribute("data-state", "connected");
    await expect(page.getByTestId("channel-shopify")).toContainText("Connected");
    await expect(page.getByTestId("channel-aliexpress")).toContainText("Connected");
    // The Integrations vocabulary (UX-L2D-06): the server has no eBay app
    // credentials, which only an operator can change.
    await expect(page.getByTestId("channel-ebay")).toContainText("Setup unavailable");
    await expect(page.getByRole("link", { name: "Manage Shopify" })).toHaveAttribute(
      "href",
      "/settings/integrations",
    );

    // Activity is the notifications list, labelled by kind.
    const activity = page.getByTestId("recent-activity");
    await expect(activity.getByRole("listitem")).toHaveCount(2);
    await expect(activity).toContainText("Import completed");
    await expect(page.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/notifications");

    await expectNoMoneyOnHome(page);
    await expectNoHorizontalOverflow(page);
    await shoot(page, "desktop-light-populated");
  });

  test("the import action is the existing import flow", async ({ page }) => {
    await mockHomeApi(page, populatedScenario());
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Import as Draft" }).click();
    await expect(page.getByRole("dialog", { name: "Import as Draft from AliExpress" })).toBeVisible();
  });

  test("keyboard reaches the next-step action with a visible focus ring", async ({ page }) => {
    await mockHomeApi(page, populatedScenario());
    await page.goto("/dashboard");
    const cta = page.getByTestId("next-step").getByRole("link");
    await expect(cta).toBeVisible();
    // Real keyboard travel, so `:focus-visible` applies as it would for a user.
    let focused = false;
    for (let i = 0; i < 80 && !focused; i += 1) {
      await page.keyboard.press("Tab");
      focused = await cta.evaluate((el) => el === document.activeElement);
    }
    expect(focused).toBe(true);
    const ring = await cta.evaluate(
      (el) => el.matches(":focus-visible") && getComputedStyle(el).boxShadow !== "none",
    );
    expect(ring).toBe(true);
  });
});

test.describe("Home — needs attention", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("lists every real problem with a link to where it is fixed", async ({ page }) => {
    await mockHomeApi(page, attentionScenario());
    await page.goto("/dashboard");

    const list = page.getByTestId("attention-list");
    await expect(list).toBeVisible();
    await expect(page.getByTestId("attention-clear")).toHaveCount(0);

    // Errors before warnings; each names its remedy.
    await expect(list.getByRole("listitem").first()).toContainText("AliExpress needs attention");
    await expect(list).toContainText("Authorization expired");
    await expect(list).toContainText("An import failed");
    await expect(list).toContainText("Supplier listing is no longer available.");
    await expect(list).toContainText("AI optimisation failed for a recent draft");
    await expect(list).toContainText("2 order syncs failed this week");
    await expect(list).toContainText("Shopify webhook rejected");
    await expect(list.getByRole("link", { name: /Open Integrations: AliExpress/ })).toHaveAttribute(
      "href",
      "/settings/integrations",
    );
    await expect(list.getByRole("link", { name: /Review imports/ })).toHaveAttribute("href", "/imports/history");
    await expect(list.getByRole("link", { name: /Open draft/ })).toHaveAttribute(
      "href",
      `/drafts/${HOME_DRAFT_IDS[0]}`,
    );

    // Severity is announced as text, not only drawn as colour.
    await expect(list.locator(".sr-only", { hasText: "Error:" })).toHaveCount(2);
    await expect(list.locator(".sr-only", { hasText: "Warning:" })).toHaveCount(4);

    // The next step follows the same rule order: a broken AliExpress
    // connection outranks "continue editing".
    await expect(page.getByTestId("next-step")).toHaveAttribute("data-rule", "connect-aliexpress");
    await expect(page.getByTestId("channel-shopify")).toContainText("Needs attention");
    await expect(page.getByTestId("channel-shopify")).toContainText("webhook setup");

    await expectNoHorizontalOverflow(page);
    await shoot(page, "desktop-light-attention");
  });
});

test.describe("Home — empty workspace", () => {
  test("shows the three setup steps instead of zeros", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop);
    await mockHomeApi(page, emptyScenario());
    await page.goto("/dashboard");

    const setup = page.getByTestId("empty-workspace");
    await expect(setup).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Set up your workspace" })).toBeVisible();
    const steps = setup.getByRole("listitem");
    await expect(steps).toHaveCount(3);
    await expect(steps.nth(0)).toContainText("Connect your Shopify store");
    await expect(steps.nth(1)).toContainText("Connect AliExpress");
    await expect(steps.nth(2)).toContainText("Import your first product");
    await expect(setup.getByRole("link", { name: "Connect Shopify" })).toHaveAttribute(
      "href",
      "/settings/integrations",
    );

    // No zero tiles, no analytics, no summary grid.
    await expect(page.getByTestId("catalogue-summary")).toHaveCount(0);
    await expect(page.locator("#main-content").getByText(/^0$/)).toHaveCount(0);
    await expectNoMoneyOnHome(page);

    // The third step is the real import dialog.
    await setup.getByRole("button", { name: "Import as Draft" }).click();
    await expect(page.getByRole("dialog", { name: "Import as Draft from AliExpress" })).toBeVisible();
    await page.keyboard.press("Escape");
    await shoot(page, "desktop-light-empty");
  });

  test("a step ticks itself off once its connection exists", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop);
    const scenario = emptyScenario();
    // Connected but still nothing imported and no products: only half set up.
    scenario.shopify = CONNECTED_SHOPIFY;
    await mockHomeApi(page, scenario);
    await page.goto("/dashboard");
    // One connected channel means the workspace is no longer "empty": the
    // grid renders and the next step moves on to the next missing piece.
    await expect(page.getByTestId("empty-workspace")).toHaveCount(0);
    await expect(page.getByTestId("next-step")).toHaveAttribute("data-rule", "connect-aliexpress");
  });

  test("works at phone width and in dark mode", async ({ browser }) => {
    const context = await browser.newContext({ viewport: VIEWPORTS.mobile, colorScheme: "dark" });
    const page = await context.newPage();
    try {
      await mockHomeApi(page, emptyScenario());
      await page.goto("/dashboard");
      await expect(page.getByTestId("empty-workspace")).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(true);
      await expectNoHorizontalOverflow(page);
      const cta = page.getByTestId("empty-workspace").getByRole("link", { name: "Connect Shopify" });
      await expect(cta).toBeInViewport();
      await shoot(page, "mobile-dark-empty");
    } finally {
      await context.close();
    }
  });
});

test.describe("Home — next step rules", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("1. no Shopify store → connect Shopify", async ({ page }) => {
    const s = populatedScenario();
    s.shopify = NO_SHOPIFY;
    await mockHomeApi(page, s);
    await page.goto("/dashboard");
    const next = page.getByTestId("next-step");
    await expect(next).toHaveAttribute("data-rule", "connect-shopify");
    await expect(next.getByRole("link", { name: "Connect Shopify" })).toHaveAttribute("href", "/settings/integrations");
    // Not connected is a next step, never an attention item.
    await expect(page.getByTestId("attention-clear")).toBeVisible();
  });

  test("2. Shopify connected, AliExpress not → connect AliExpress", async ({ page }) => {
    const s = populatedScenario();
    s.aliexpress = NO_ALIEXPRESS;
    await mockHomeApi(page, s);
    await page.goto("/dashboard");
    await expect(page.getByTestId("next-step")).toHaveAttribute("data-rule", "connect-aliexpress");
  });

  test("3. both connected, no drafts → import the first product", async ({ page }) => {
    const s = populatedScenario();
    s.counts = { drafts: 0, products: 0 };
    s.drafts = [];
    s.aliexpress = CONNECTED_ALIEXPRESS;
    await mockHomeApi(page, s);
    await page.goto("/dashboard");
    const next = page.getByTestId("next-step");
    await expect(next).toHaveAttribute("data-rule", "import-first-product");
    await expect(page.getByTestId("recent-drafts-empty")).toBeVisible();
  });

  test("4. drafts exist → continue the most recently edited one", async ({ page }) => {
    await mockHomeApi(page, populatedScenario());
    await page.goto("/dashboard");
    const next = page.getByTestId("next-step");
    await expect(next).toHaveAttribute("data-rule", "continue-draft");
    await expect(next).toContainText("Wireless Desk Lamp with USB Charging");
  });
});

test.describe("Home — loading and partial failure", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("holds its shape while the gating requests load", async ({ page }) => {
    const s = populatedScenario();
    s.delayMs = { counts: 1500 };
    await mockHomeApi(page, s);
    await page.goto("/dashboard");
    const loading = page.getByTestId("home-loading");
    await expect(loading).toBeVisible();
    await expect(loading.locator("[aria-busy='true']").first()).toBeVisible();
    await expect(page.getByTestId("summary-drafts")).toBeVisible({ timeout: 10_000 });
    await expect(loading).toHaveCount(0);
  });

  test("one failed block does not take the page down, and can retry", async ({ page }) => {
    const s = populatedScenario();
    s.fail = new Set(["notifications"]);
    await mockHomeApi(page, s);
    await page.goto("/dashboard");

    // Everything else renders.
    await expect(page.getByTestId("summary-drafts")).toContainText("7");
    await expect(page.getByTestId("recent-drafts")).toBeVisible();
    await expect(page.getByTestId("channel-status")).toBeVisible();

    // The failed block says so, locally, as an alert with a retry.
    const activity = page.getByTestId("home-activity");
    const alert = activity.getByRole("alert");
    await expect(alert).toContainText("Couldn’t load recent activity");

    s.fail.clear();
    await alert.getByRole("button", { name: "Retry" }).click();
    await expect(activity.getByTestId("recent-activity")).toBeVisible();
    await expect(activity.getByRole("alert")).toHaveCount(0);
  });

  test("a failed gating request shows one clear error instead of a broken layout", async ({ page }) => {
    const s = populatedScenario();
    s.fail = new Set(["counts"]);
    await mockHomeApi(page, s);
    await page.goto("/dashboard");
    const alert = page.locator("#main-content").getByRole("alert");
    await expect(alert).toContainText("Couldn’t load your workspace");
    await expect(page.getByTestId("empty-workspace")).toHaveCount(0);
    await expect(page.getByTestId("catalogue-summary")).toHaveCount(0);
  });
});

test.describe("Home — responsive", () => {
  for (const [name, viewport] of Object.entries(VIEWPORTS)) {
    test(`populated Home fits ${name} in light mode`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await mockHomeApi(page, attentionScenario());
      await page.goto("/dashboard");
      await expect(page.getByTestId("attention-list")).toBeVisible();
      await expectNoHorizontalOverflow(page);

      // The primary action stays reachable and large enough to tap.
      const cta = page.getByTestId("next-step").getByRole("link");
      const box = await cta.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(36);
      const edit = page.getByTestId("recent-drafts").getByRole("link", { name: /^Edit / }).first();
      const editBox = await edit.boundingBox();
      expect(editBox?.height ?? 0).toBeGreaterThanOrEqual(32);

      // Status text is never clipped: each channel row's words are visible.
      for (const id of ["shopify", "aliexpress", "ebay"]) {
        await expect(page.getByTestId(`channel-${id}`)).toBeVisible();
      }
      await shoot(page, `${name}-light-attention`);
    });
  }

  test("populated Home in dark mode at desktop", async ({ browser }) => {
    const context = await browser.newContext({ viewport: VIEWPORTS.desktop, colorScheme: "dark" });
    const page = await context.newPage();
    try {
      await mockHomeApi(page, populatedScenario());
      await page.goto("/dashboard");
      await expect(page.getByTestId("recent-drafts")).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(true);
      await shoot(page, "desktop-dark-populated");
    } finally {
      await context.close();
    }
  });
});
