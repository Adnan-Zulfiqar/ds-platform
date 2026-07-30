import { expect, test } from "@playwright/test";

/**
 * Smoke tests for the application shell.
 *
 * Phase 1 gated the dashboard behind authentication, so the tests that
 * previously asserted on dashboard content now assert on the redirect instead.
 * Signed-in dashboard behaviour needs a running backend and a real account, so
 * it is covered by the backend integration suite rather than mocked here —
 * a mocked session would test the mock.
 */

test.describe("Application shell", () => {
  test("root redirects an unauthenticated visitor to sign-in", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });

  test("sign-in page renders with the correct title", async ({ page }) => {
    await page.goto("/login");

    await expect(page).toHaveTitle(/DropPilot AI/);
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  });

  test("an unknown route renders the 404 page", async ({ page }) => {
    const response = await page.goto("/this-route-does-not-exist");

    expect(response?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });
});

test.describe("Accessibility", () => {
  test("a skip link is the first focusable element", async ({ page }) => {
    await page.goto("/login");
    await page.keyboard.press("Tab");

    await expect(
      page.getByRole("link", { name: "Skip to main content" }),
    ).toBeFocused();
  });

  test("the page exposes a main landmark", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("main")).toBeVisible();
  });
});

test.describe("Theme", () => {
  test("renders in both colour schemes without error", async ({ page }) => {
    for (const scheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto("/login");
      await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    }
  });
});
