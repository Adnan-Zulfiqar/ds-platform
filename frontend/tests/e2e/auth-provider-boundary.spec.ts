import { expect, test } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * EBAY-C1.1 follow-up — where `AuthProvider` mounts.
 *
 * It used to sit in the root layout, so every route ran a session bootstrap,
 * including the public privacy policy. It now mounts in `app/(auth)/layout.tsx`
 * and `app/(protected)/layout.tsx` instead.
 *
 * These tests hold both halves of that boundary in place. `privacy.spec.ts`
 * proves the public side performs no bootstrap; this file proves the move did
 * not quietly disable authentication everywhere else — which is the failure a
 * green privacy suite would otherwise hide.
 */

const REFRESH = "/api/v1/auth/refresh";

/** Records paths of auth calls the page makes, in order. */
function trackAuthCalls(page: import("@playwright/test").Page): string[] {
  const calls: string[] = [];
  page.on("request", (r) => {
    const path = new URL(r.url()).pathname;
    if (path.includes("/auth/")) calls.push(path);
  });
  return calls;
}

test.describe("AuthProvider still mounts where a session is needed", () => {
  test("the sign-in page bootstraps the session", async ({ page }) => {
    test.skip(
      !(await isApiReachable()),
      "Backend API is not reachable — start it to run this test.",
    );

    // `/login` needs it: an already-authenticated visitor is redirected away
    // from the form, and that decision depends on the restored status.
    const calls = trackAuthCalls(page);

    await page.goto("/login");
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
    await page.waitForTimeout(1500);

    expect(calls).toContain(REFRESH);
  });

  test("the register page bootstraps the session", async ({ page }) => {
    test.skip(
      !(await isApiReachable()),
      "Backend API is not reachable — start it to run this test.",
    );

    const calls = trackAuthCalls(page);

    await page.goto("/register");
    await page.waitForTimeout(1500);

    expect(calls).toContain(REFRESH);
  });

  test("a protected page bootstraps and restores the session across a reload", async ({
    page,
  }) => {
    test.skip(
      !(await isApiReachable()),
      "Backend API is not reachable — start it to run this test.",
    );

    await registerAndSignIn(page);
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/dashboard/);
    // Settle the shell before reloading. Without this the reload can race the
    // first paint, which fails for timing reasons rather than session ones.
    await expect(
      page.getByRole("button", { name: "Account menu" }),
    ).toBeVisible({ timeout: 30000 });

    // The access token is memory-only, so surviving a reload proves the
    // provider mounted and exchanged the refresh cookie.
    const calls = trackAuthCalls(page);
    await page.reload();

    await expect(page).toHaveURL(/\/dashboard/);
    await expect(
      page.getByRole("button", { name: "Account menu" }),
    ).toBeVisible({ timeout: 30000 });
    expect(calls).toContain(REFRESH);
  });
});

test.describe("Protected routes are still enforced", () => {
  test("an unauthenticated visitor is sent to sign in", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test("signing out returns to sign in and does not go back", async ({ page }) => {
    test.skip(
      !(await isApiReachable()),
      "Backend API is not reachable — start it to run this test.",
    );

    await registerAndSignIn(page);
    await page.goto("/dashboard");

    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitem", { name: "Log out" }).click();

    await expect(page).toHaveURL(/\/login/, { timeout: 20000 });

    // `replace`, not `push` — back must not return to an authenticated screen.
    await page.goBack();
    await expect(page).not.toHaveURL(/\/dashboard/);
  });
});

test.describe("Public routes outside both groups stay clean", () => {
  for (const route of ["/privacy", "/unauthorized"]) {
    test(`${route} performs no session bootstrap`, async ({ page }) => {
      const calls = trackAuthCalls(page);

      await page.goto(route);
      await page.waitForTimeout(1500);

      expect(calls).toEqual([]);
    });
  }
});
