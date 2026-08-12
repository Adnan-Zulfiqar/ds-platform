import { expect, test } from "@playwright/test";

import { isApiReachable, registerAndSignIn } from "./helpers/auth";

/**
 * Import History — retry action for failed imports (DSers-parity M1 gap-fix).
 *
 * Mocked at the network boundary rather than seeded through a live AliExpress
 * failure: forcing a genuine provider failure (rate limit, ship-to rejection,
 * timeout) on demand isn't reliable against the real gateway, and the
 * behaviour under test is the UI's reaction to a `failed` row + a
 * retry response — not the provider's failure modes themselves, which
 * `test_products.py::TestRetryImport` already covers server-side.
 */

const FAILED_IMPORT = {
  id: "22222222-2222-2222-2222-222222222222",
  source: "aliexpress",
  externalId: "1005010486653604",
  status: "failed",
  productId: null,
  errorCode: "aliexpress_ship_to_prohibited",
  errorMessage: "This product cannot currently be shipped to your destination.",
  startedAt: new Date().toISOString(),
  finishedAt: new Date().toISOString(),
  createdAt: new Date().toISOString(),
};

function pageOf<T>(items: T[]) {
  return {
    items,
    meta: {
      page: 1,
      size: 50,
      totalItems: items.length,
      totalPages: 1,
      hasNext: false,
      hasPrevious: false,
    },
  };
}

test.beforeAll(async () => {
  test.skip(
    !(await isApiReachable()),
    "Backend API is not reachable — start it to run import history tests.",
  );
});

test.describe("Import History page", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("shows an empty state before any import attempt", async ({ page }) => {
    await registerAndSignIn(page);
    await page.route("**/products/imports*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(pageOf([])),
      }),
    );

    await page.goto("/imports/history");
    await expect(page.getByText("No imports yet")).toBeVisible();
  });

  test("shows a failed import with a retry action", async ({ page }) => {
    await registerAndSignIn(page);
    await page.route("**/products/imports*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(pageOf([FAILED_IMPORT])),
      }),
    );

    await page.goto("/imports/history");

    const row = page.getByTestId("import-row");
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("failed");
    await expect(row).toContainText(FAILED_IMPORT.errorMessage);
    await expect(page.getByTestId("retry-import-button")).toBeVisible();
  });

  test("retrying a failed import shows the resulting draft", async ({ page }) => {
    await registerAndSignIn(page);
    await page.route("**/products/imports*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(pageOf([FAILED_IMPORT])),
      }),
    );

    const retriedDraftId = "33333333-3333-3333-3333-333333333333";
    let retryRequested = false;
    await page.route(
      `**/products/imports/${FAILED_IMPORT.id}/retry`,
      (route) => {
        retryRequested = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: retriedDraftId,
            source: "aliexpress",
            externalId: FAILED_IMPORT.externalId,
            title: "Anti-Snoring Mouthpiece",
            status: "draft",
            stockQuantity: 0,
            tags: [],
            aiStatus: "not_optimized",
            variants: [],
            images: [],
            createdAt: new Date().toISOString(),
          }),
        });
      },
    );

    await page.goto("/imports/history");
    await page.getByTestId("retry-import-button").click();

    await expect(page.getByTestId("retry-success-link")).toBeVisible();
    expect(retryRequested).toBe(true);
    await expect(
      page.getByTestId("retry-success-link"),
    ).toHaveAttribute("href", `/drafts/${retriedDraftId}`);
  });

  test("surfaces a retry failure without losing the row", async ({ page }) => {
    await registerAndSignIn(page);
    await page.route("**/products/imports*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(pageOf([FAILED_IMPORT])),
      }),
    );
    await page.route(
      `**/products/imports/${FAILED_IMPORT.id}/retry`,
      (route) =>
        route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({
            code: "aliexpress_unavailable",
            message: "AliExpress is temporarily unavailable. Try again shortly.",
            details: [],
            requestId: "test-req-retry-502",
          }),
        }),
    );

    await page.goto("/imports/history");
    await page.getByTestId("retry-import-button").click();

    // Not a bare getByRole("alert") — Next.js's App Router route-announcer
    // (#__next-route-announcer__) always carries role="alert" too, so an
    // unscoped query is ambiguous the moment this page has navigated at all.
    await expect(
      page.getByRole("alert").filter({ hasText: /temporarily unavailable/i }),
    ).toBeVisible();
    // The retry control stays available — a failed retry must not strand the row.
    await expect(page.getByTestId("retry-import-button")).toBeEnabled();
  });

  test("redirects an unauthenticated visitor to sign in", async ({ page }) => {
    await page.goto("/imports/history");
    await expect(page).toHaveURL(/\/login/);
  });
});
