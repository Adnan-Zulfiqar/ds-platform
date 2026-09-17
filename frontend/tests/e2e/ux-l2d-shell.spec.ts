import path from "node:path";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { resolveSuiteShotRoot } from "./helpers/evidence-paths";
import { SHELL_DRAFT_ID, mockShellApi } from "./helpers/shell-fixture";
import {
  ALL_NAV_ITEMS,
  NAV_FOOTER_ITEMS,
  NAV_SECTIONS,
  PLANNED_NAV_ITEMS,
} from "../../lib/navigation";

/**
 * UX-L2D-02 — application shell.
 *
 * Backend-less: every API call is answered by `mockShellApi`, so these run
 * anywhere and prove shell behaviour rather than data. Each assertion maps to
 * a UX-L2D-01 finding:
 *
 * - F-5  every protected page has the same gutter (PageContainer at the layout)
 * - F-7  the sidebar keeps Settings reachable at laptop heights, fades the
 *        edge that hides items, and no longer carries dead controls or
 *        placeholders for unbuilt destinations
 * - IA   the manifest is job-based, every href resolves, nested routes get a
 *        breadcrumb
 *
 * Screenshots are evidence, not assertions; they land under
 * `test-results/ux-l2d-shell` unless `UX_L2D_SHOT_ROOT` says otherwise.
 */

const SHOT_ROOT = resolveSuiteShotRoot("ux-l2d-shell", ["UX_L2D_SHOT_ROOT"]);

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 1024, height: 768 },
  mobile: { width: 390, height: 844 },
} as const;

/** Routes whose gutter was missing before UX-L2D-02, plus two that had one. */
const GUTTER_ROUTES = [
  "/dashboard",
  "/drafts",
  "/products",
  "/imports/history",
  `/drafts/${SHELL_DRAFT_ID}`,
  "/settings/integrations",
] as const;

test.beforeEach(async ({ page }) => {
  await mockShellApi(page);
});

async function shoot(page: Page, name: string) {
  // Let open/close transitions finish so a frame mid-slide is never mistaken
  // for a layout defect in the evidence.
  await page.evaluate(() =>
    Promise.all(document.getAnimations().map((a) => a.finished.catch(() => undefined))),
  );
  await page.screenshot({ path: path.join(SHOT_ROOT, `${name}.png`), fullPage: false });
}

for (const [viewportName, viewport] of Object.entries(VIEWPORTS)) {
  test.describe(`Gutters at ${viewportName}`, () => {
    test.use({ viewport });

    for (const route of GUTTER_ROUTES) {
      test(`${route} content is inset from the shell edges`, async ({ page }) => {
        await page.goto(route);
        const container = page.getByTestId("page-container");
        await expect(container).toBeVisible();

        // The container spans `main`; its padding is the gutter. Measure the
        // rendered inset of the first child rather than trusting a class name.
        const inset = await container.evaluate((el) => {
          const main = document.getElementById("main-content");
          const child = el.firstElementChild;
          if (!main || !child) return null;
          const m = main.getBoundingClientRect();
          const c = child.getBoundingClientRect();
          return { left: c.left - m.left, right: m.right - c.right };
        });
        expect(inset).not.toBeNull();
        if (!inset) return;

        // Tailwind `p-4` below `sm`, `p-6` from `sm` up.
        const expected = viewport.width >= 640 ? 24 : 16;
        expect(inset.left).toBeGreaterThanOrEqual(expected);
        expect(inset.right).toBeGreaterThanOrEqual(expected);

        // The document itself never scrolls sideways; `main` may only do so on
        // the editor, whose tab strip is an editor concern (UX-L2D-05).
        const overflow = await page.evaluate(() => ({
          document: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          main: (() => {
            const el = document.getElementById("main-content");
            return el ? el.scrollWidth > el.clientWidth : false;
          })(),
        }));
        expect(overflow.document).toBe(false);
        if (!route.startsWith(`/drafts/${SHELL_DRAFT_ID}`)) {
          expect(overflow.main).toBe(false);
        }

        await shoot(page, `${viewportName}-${route.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "")}`);
      });
    }
  });
}

test.describe("Navigation manifest", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("renders every section heading, every item, and the pinned footer", async ({ page }) => {
    await page.goto("/dashboard");
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav).toBeVisible();

    for (const section of NAV_SECTIONS) {
      if (section.label) {
        await expect(nav.getByRole("heading", { name: section.label })).toBeVisible();
      }
      for (const item of section.items) {
        await expect(nav.getByRole("link", { name: new RegExp(`^${item.label}`) })).toBeVisible();
      }
    }

    const footer = page.getByRole("navigation", { name: "Secondary navigation" });
    for (const item of NAV_FOOTER_ITEMS) {
      await expect(footer.getByRole("link", { name: item.label })).toBeVisible();
    }
  });

  test("planned destinations are not offered as navigation", async ({ page }) => {
    // A placeholder in primary navigation advertises what does not exist.
    // The routes that do exist (`/customers`) still answer by URL.
    await page.goto("/dashboard");
    for (const planned of PLANNED_NAV_ITEMS) {
      await expect(page.getByRole("link", { name: planned.label })).toHaveCount(0);
      await expect(page.getByRole("navigation").getByText(planned.label)).toHaveCount(0);
    }
    await page.goto("/customers");
    await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
  });

  test("every navigation href resolves to a real page", async ({ page }) => {
    for (const item of ALL_NAV_ITEMS) {
      const response = await page.goto(item.href);
      expect(response?.status(), item.href).toBe(200);
      await expect(page.getByRole("heading", { name: "Page not found" }), item.href).toHaveCount(0);
      await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    }
  });

  test("the top bar carries no disabled placeholder controls", async ({ page }) => {
    await page.goto("/dashboard");
    const header = page.locator("header");
    await expect(header.getByRole("button", { name: /coming soon/i })).toHaveCount(0);
    await expect(header.locator("button[disabled]")).toHaveCount(0);
  });

  test("nested routes get a section › page breadcrumb", async ({ page }) => {
    await page.goto("/settings/integrations");
    const crumb = page.getByRole("navigation", { name: "Breadcrumb" });
    await expect(crumb).toContainText("Channels");
    await expect(crumb.locator("[aria-current='page']")).toHaveText("Integrations");

    // Settings itself is exact-match: the parent must not light up on a child.
    const footer = page.getByRole("navigation", { name: "Secondary navigation" });
    await expect(footer.getByRole("link", { name: "Settings" })).not.toHaveAttribute(
      "aria-current",
      "page",
    );
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: "Integrations" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await page.goto(`/drafts/${SHELL_DRAFT_ID}`);
    await expect(crumb).toContainText("Catalogue");
    await expect(crumb.locator("[aria-current='page']")).toHaveText("Drafts");
    await expect(nav.getByRole("link", { name: /^Drafts/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});

test.describe("Sidebar at laptop heights", () => {
  test("1440×900: everything is reachable without scrolling the list", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop);
    await page.goto("/dashboard");

    const region = page.getByTestId("nav-scroll-region");
    await expect(region).toHaveAttribute("data-scroll-bottom", "false");

    const settings = page
      .getByRole("navigation", { name: "Secondary navigation" })
      .getByRole("link", { name: "Settings" });
    await expect(settings).toBeInViewport();
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeInViewport();
    // The last section item is on screen too.
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: "Analytics" })).toBeInViewport();
    await shoot(page, "desktop-900-sidebar");
  });

  test("1440×640: the list scrolls, says so, and Settings stays pinned", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 640 });
    await page.goto("/dashboard");

    const region = page.getByTestId("nav-scroll-region");
    await expect(region).toHaveAttribute("data-scroll-bottom", "true");

    const settings = page
      .getByRole("navigation", { name: "Secondary navigation" })
      .getByRole("link", { name: "Settings" });
    await expect(settings).toBeInViewport();
    await expect(page.getByRole("button", { name: "Collapse sidebar" })).toBeInViewport();

    // Scrolling the list to the end reveals the last item and clears the fade.
    await region.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await expect(region).toHaveAttribute("data-scroll-bottom", "false");
    await expect(region).toHaveAttribute("data-scroll-top", "true");
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: "Analytics" })).toBeInViewport();
    await shoot(page, "desktop-640-sidebar-scrolled");
  });

  test("collapsed rail keeps Settings and the expand control", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.desktop);
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
    const settings = page
      .getByRole("navigation", { name: "Secondary navigation" })
      .getByRole("link", { name: "Settings" });
    await expect(settings).toBeVisible();
    await shoot(page, "desktop-collapsed");
  });
});

test.describe("Mobile drawer", () => {
  test.use({ viewport: VIEWPORTS.mobile });

  test("offers every section and the pinned Settings, then closes on navigation", async ({
    page,
  }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeHidden();
    await page.getByRole("button", { name: "Open navigation menu" }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();

    for (const section of NAV_SECTIONS) {
      if (section.label) {
        await expect(drawer.getByRole("heading", { name: section.label })).toBeVisible();
      }
    }
    await expect(drawer.getByRole("link", { name: "Settings" })).toBeInViewport();
    await shoot(page, "mobile-drawer");

    await drawer.getByRole("link", { name: "Orders", exact: true }).click();
    await expect(page).toHaveURL(/\/orders$/);
    await expect(drawer).toBeHidden();
  });
});

test.describe("Dark mode", () => {
  test.use({ viewport: VIEWPORTS.desktop, colorScheme: "dark" });

  test("shell renders with the dark palette and the same gutters", async ({ page }) => {
    await page.goto("/drafts");
    await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(true);
    const inset = await page.getByTestId("page-container").evaluate((el) => {
      const main = document.getElementById("main-content");
      const child = el.firstElementChild;
      if (!main || !child) return null;
      return child.getBoundingClientRect().left - main.getBoundingClientRect().left;
    });
    expect(inset).not.toBeNull();
    expect(inset ?? 0).toBeGreaterThanOrEqual(24);
    await shoot(page, "desktop-dark-drafts");
  });
});
