import { expect, test } from "@playwright/test";

/**
 * Smoke tests for the Phase 0 shell.
 *
 * These assert on user-visible behaviour — headings, navigation landmarks,
 * responsive layout — rather than on CSS classes or component internals, so
 * they survive a restyle and only fail when something a user would notice
 * actually breaks.
 */

test.describe("Application shell", () => {
  test("root redirects to the dashboard", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("dashboard renders its heading and title", async ({ page }) => {
    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: "Dashboard", level: 1 }),
    ).toBeVisible();
    await expect(page).toHaveTitle(/DropPilot AI/);
  });

  test("foundation status is conveyed as text, not colour alone", async ({
    page,
  }) => {
    await page.goto("/dashboard");

    // Accessibility requirement: a status communicated only by hue is invisible
    // to colour-blind users.
    await expect(page.getByText("Ready").first()).toBeVisible();
    await expect(page.getByText("Planned").first()).toBeVisible();
  });

  test("an unknown route renders the 404 page", async ({ page }) => {
    const response = await page.goto("/this-route-does-not-exist");

    expect(response?.status()).toBe(404);
    await expect(
      page.getByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
  });
});

test.describe("Navigation", () => {
  test("sidebar is visible on desktop", async ({ page }) => {
    await page.goto("/dashboard");

    await expect(
      page.getByRole("navigation", { name: "Main navigation" }),
    ).toBeVisible();
  });

  test("dashboard link is marked as the current page", async ({ page }) => {
    await page.goto("/dashboard");

    const link = page.getByRole("link", { name: "Dashboard" });
    await expect(link).toHaveAttribute("aria-current", "page");
  });
});

test.describe("Accessibility", () => {
  test("a skip link is the first focusable element", async ({ page }) => {
    await page.goto("/dashboard");
    await page.keyboard.press("Tab");

    await expect(
      page.getByRole("link", { name: "Skip to main content" }),
    ).toBeFocused();
  });

  test("the page exposes a main landmark", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("main")).toBeVisible();
  });
});
