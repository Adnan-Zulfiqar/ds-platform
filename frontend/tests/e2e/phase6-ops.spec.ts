import { expect, test, type Page } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * Phase 6 pages render inside the shell with a real heading.
 *
 * These are smoke checks against live routes — not full workflow coverage.
 * Create/sync flows that need AliExpress stay in backend integration tests.
 */

const ROUTES = [
  "/inventory",
  "/pricing",
  "/stores",
  "/automation",
  "/notifications",
  "/analytics",
  "/shipments",
] as const;

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run Phase 6 ops tests.",
  );
});

async function signIn(page: Page): Promise<void> {
  await registerAndSignIn(page);
}

test.describe("Phase 6 operations pages", () => {
  test("each ops route shows its page heading", async ({ page }) => {
    await signIn(page);

    for (const route of ROUTES) {
      await page.goto(route);
      await expect(page).toHaveURL(new RegExp(`${route}$`));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    }
  });

  test("dashboard no longer shows the sample-data banner", async ({ page }) => {
    await signIn(page);
    await page.goto("/dashboard");
    await expect(page.getByText("Sample data")).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});
