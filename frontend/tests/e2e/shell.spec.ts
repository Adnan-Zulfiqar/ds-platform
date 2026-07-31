import { expect, test, type Page } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * Application shell tests.
 *
 * These require a signed-in session, so they need the backend and its database
 * running. When the API is unreachable the whole file skips with a clear
 * reason — a skipped suite is honest, a suite that fails for environmental
 * reasons trains people to ignore red.
 *
 * Assertions target roles, labels, and visible text rather than CSS classes, so
 * a restyle does not break them and a genuine regression does.
 */

const VIEWPORTS = {
  mobile: { width: 320, height: 720 },
  tablet: { width: 768, height: 1024 },
  desktop: { width: 1440, height: 900 },
} as const;

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run shell tests.",
  );
});

async function signIn(page: Page): Promise<void> {
  await registerAndSignIn(page);
}

test.describe("Sidebar", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("renders every navigation section", async ({ page }) => {
    await signIn(page);

    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav).toBeVisible();

    for (const section of [
      "Main",
      "Product Management",
      "Sales",
      "Stores",
      "Analytics",
      "System",
    ]) {
      // Matched by role, not text: several section names ("Stores",
      // "Analytics") are also link labels inside the same landmark, so a text
      // query would be ambiguous.
      await expect(nav.getByRole("heading", { name: section })).toBeVisible();
    }
  });

  test("marks the current route as active", async ({ page }) => {
    await signIn(page);

    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("updates the active item after navigating", async ({ page }) => {
    await signIn(page);

    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await nav.getByRole("link", { name: "Products", exact: true }).click();
    await expect(page).toHaveURL(/\/products$/);

    await expect(
      nav.getByRole("link", { name: "Products", exact: true }),
    ).toHaveAttribute("aria-current", "page");
  });

  test("unbuilt destinations are disabled, not links", async ({ page }) => {
    /**
     * The property that keeps navigation from ever reaching a 404: a
     * `coming-soon` item must not be a link at all.
     */
    await signIn(page);
    const nav = page.getByRole("navigation", { name: "Main navigation" });

    await expect(nav.getByRole("link", { name: "Suppliers" })).toHaveCount(0);
    await expect(nav.getByText("Suppliers")).toBeVisible();
  });

  test("collapses and expands, persisting across reload", async ({ page }) => {
    await signIn(page);

    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  });

  test("labels are replaced by tooltips when collapsed", async ({ page }) => {
    await signIn(page);
    await page.getByRole("button", { name: "Collapse sidebar" }).click();

    // The link keeps its accessible name via sr-only text even though the
    // visible label is gone — a tooltip alone would leave touch users with an
    // unlabelled icon.
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();
  });
});

test.describe("Mobile navigation", () => {
  test.use({ viewport: VIEWPORTS.mobile });

  test("sidebar is hidden and a menu button is offered", async ({ page }) => {
    await signIn(page);

    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeHidden();
    await expect(page.getByRole("button", { name: "Open navigation menu" })).toBeVisible();
  });

  test("drawer opens and shows the same navigation", async ({ page }) => {
    await signIn(page);
    await page.getByRole("button", { name: "Open navigation menu" }).click();

    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("link", { name: "Products", exact: true })).toBeVisible();
  });

  test("drawer closes after navigating", async ({ page }) => {
    // A drawer left open over the page the user just asked for hides it.
    await signIn(page);
    await page.getByRole("button", { name: "Open navigation menu" }).click();
    await page.getByRole("link", { name: "Orders", exact: true }).click();

    await expect(page).toHaveURL(/\/orders$/);
    await expect(page.getByRole("dialog")).toBeHidden();
  });

  test("drawer closes on Escape", async ({ page }) => {
    await signIn(page);
    await page.getByRole("button", { name: "Open navigation menu" }).click();
    await page.keyboard.press("Escape");

    await expect(page.getByRole("dialog")).toBeHidden();
  });

  test("page does not scroll horizontally at 320px", async ({ page }) => {
    // The narrowest viewport worth supporting. Horizontal overflow here is the
    // most common responsive failure and is invisible on a desktop screen.
    await signIn(page);

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflows).toBe(false);
  });
});

test.describe("Tablet layout", () => {
  test.use({ viewport: VIEWPORTS.tablet });

  test("sidebar is visible at the md breakpoint", async ({ page }) => {
    await signIn(page);
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
  });
});

test.describe("User menu", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("shows identity, tenant, and role", async ({ page }) => {
    const account = await registerAndSignIn(page);
    await page.getByRole("button", { name: "Account menu" }).click();

    // Scoped to the open menu: the tenant name also appears elsewhere in the
    // shell, so an unscoped query would be ambiguous.
    const menu = page.getByRole("menu");
    await expect(menu.getByText(account.email)).toBeVisible();
    await expect(menu.getByText(account.companyName)).toBeVisible();
    // The first user of a tenant is always the owner.
    await expect(menu.getByText("owner", { exact: true })).toBeVisible();
  });

  test("signing out returns to the login page", async ({ page }) => {
    await signIn(page);

    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitem", { name: /Log out/ }).click();

    await page.waitForURL(/\/login/, { timeout: 15_000 });
  });

  test("a signed-out user cannot return to the dashboard", async ({ page }) => {
    // The back button must not restore an authenticated screen.
    await signIn(page);
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitem", { name: /Log out/ }).click();
    await page.waitForURL(/\/login/, { timeout: 15_000 });

    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("Dashboard", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("renders the heading and every live stat card", async ({ page }) => {
    await signIn(page);

    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    const metrics = page.getByRole("region", { name: "Key metrics" });
    for (const label of [
      "Revenue",
      "Orders",
      "Products",
      "Stores",
      "Inventory units",
      "Automation runs (7d)",
    ]) {
      await expect(metrics.getByText(label, { exact: true })).toBeVisible();
    }
  });

  test("does not present invented sample-data figures", async ({ page }) => {
    // Phase 6 removed MOCK_* — the sample-data banner must stay gone.
    await signIn(page);
    await expect(page.getByText("Sample data")).toHaveCount(0);
  });

  test("renders chart sections for live analytics", async ({ page }) => {
    await signIn(page);

    // Matched by role: "Orders" is also a nav link and a stat card label, so a
    // text query resolves to three elements. Empty tenants show empty states
    // rather than Recharts — headings prove the sections mounted.
    for (const title of ["Sales overview", "Orders", "Top products"]) {
      await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    }
  });
});

test.describe("Protected routes", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("every built route renders inside the shell", async ({ page }) => {
    await signIn(page);

    // Keep this list short — Phase 6 ops routes are covered in phase6-ops.spec.
    // Hard navigations remount the app; refresh must succeed for each hop.
    for (const route of ["/products", "/stores", "/orders", "/analytics", "/settings"]) {
      await page.goto(route);
      await expect(page).toHaveURL(new RegExp(`${route}$`));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      // The shell persists across navigation rather than the page reloading.
      await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    }
  });

  test("placeholder pages state plainly that they are not built", async ({ page }) => {
    // Phase 6 built Stores/Inventory/etc.; Customers remains ComingSoon.
    await signIn(page);
    await page.goto("/customers");

    await expect(page.getByText("Not available yet")).toBeVisible();
  });
});

test.describe("Theme switching", () => {
  test.use({ viewport: VIEWPORTS.desktop });

  test("switches between light and dark, and persists", async ({ page }) => {
    await signIn(page);

    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);

    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);

    await page.getByRole("button", { name: "Toggle theme" }).click();
    await page.getByRole("menuitem", { name: "Light" }).click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
  });

  test("the shell renders in both themes", async ({ page }) => {
    await signIn(page);

    for (const theme of ["Dark", "Light"] as const) {
      await page.getByRole("button", { name: "Toggle theme" }).click();
      await page.getByRole("menuitem", { name: theme }).click();

      await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    }
  });
});
