import { expect, test, type Page, type Route } from "@playwright/test";

import { isApiReachable, type TestAccount } from "./helpers/auth";
import { seedCatalogueViaApi, signInWithAccount } from "./helpers/catalogue";

/** Prefer the currently visible control when responsive duplicates stay in the DOM. */
function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

/**
 * Draft editor — optimistic-concurrency save flow (M2A).
 *
 * A genuine two-editor race is exercised manually against a live backend
 * (two real tabs both hit the real per-request transaction, so `updatedAt`
 * really does advance between them -- see the note in
 * `test_draft_editor_concurrency.py` about why a *test-fixture* session
 * cannot show that reliably). Here, a real authenticated session and a real
 * seeded draft are used, with only the specific `PATCH /drafts/{id}` call
 * intercepted to deterministically produce a 409 -- the same "mock one
 * endpoint inside a real session" approach `import-history.spec.ts`
 * established for the M1 duplicate-warning tests.
 */

async function openSeededDraftEditor(
  page: Page,
): Promise<{ productId: string; account: TestAccount }> {
  const apiUp = await isApiReachable();
  test.skip(!apiUp, "API not reachable at E2E_API_URL / default.");

  const existingId = process.env.E2E_PRODUCT_ID;
  const existingEmail = process.env.E2E_EMAIL;
  const existingPassword = process.env.E2E_PASSWORD;

  if (existingId && existingEmail && existingPassword) {
    const account: TestAccount = {
      email: existingEmail,
      password: existingPassword,
      companyName: "E2E Existing",
    };
    await signInWithAccount(page, account, `/drafts/${existingId}`);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    return { productId: existingId, account };
  }

  const seeded = await seedCatalogueViaApi(page.request);
  test.skip(
    seeded === null,
    "Catalogue seeding failed — set E2E_PRODUCT_ID/E2E_EMAIL/E2E_PASSWORD or enable AliExpress import.",
  );
  await signInWithAccount(page, seeded!.account, `/drafts/${seeded!.product.id}`);
  await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
  return { productId: seeded!.product.id, account: seeded!.account };
}

test.describe("Draft editor — save and conflict handling", () => {
  test("successful save clears the unsaved-changes state", async ({ page }) => {
    await openSeededDraftEditor(page);

    const titleInput = page.getByTestId("draft-title-input");
    await titleInput.fill("A freshly edited title");
    await visibleTestId(page, "save-draft").click();

    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
  });

  test("a 409 conflict shows the banner with both recovery actions, not a generic error", async ({
    page,
  }) => {
    await openSeededDraftEditor(page);

    let patchCount = 0;
    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      if (route.request().method() !== "PATCH") {
        await route.continue();
        return;
      }
      patchCount += 1;
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "conflict",
          message: "This draft was changed since you loaded it.",
          details: [],
        }),
      });
    });

    const titleInput = page.getByTestId("draft-title-input");
    await titleInput.fill("My unsaved edit");
    await visibleTestId(page, "save-draft").click();

    const banner = page.getByTestId("draft-conflict-banner");
    await expect(banner).toBeVisible();
    await expect(page.getByTestId("conflict-reload-latest")).toBeVisible();
    await expect(page.getByTestId("conflict-keep-mine")).toBeVisible();
    expect(patchCount).toBe(1);

    // The generic form-error alert must not also render for the same
    // failure -- a conflict is a distinct state with its own recovery UI,
    // not a variant of "something went wrong, try again".
    await expect(page.getByText("Save failed.")).toHaveCount(0);
  });

  test('"Reload latest version" discards local edits and adopts the server value', async ({
    page,
  }) => {
    await openSeededDraftEditor(page);
    const originalTitle = await page.getByTestId("draft-title-input").inputValue();

    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      const request = route.request();
      if (request.method() === "PATCH") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ code: "conflict", message: "Stale.", details: [] }),
        });
        return;
      }
      await route.continue();
    });

    await page.getByTestId("draft-title-input").fill("Local edit that should be discarded");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    // Unblock the underlying GET so "Reload latest" can actually refetch.
    await page.unroute("**/api/v1/drafts/*");

    await page.getByTestId("conflict-reload-latest").click();
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(originalTitle);
  });

  test('"Keep my changes" preserves the unsaved title while dismissing the conflict', async ({
    page,
  }) => {
    await openSeededDraftEditor(page);

    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      const request = route.request();
      if (request.method() === "PATCH") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ code: "conflict", message: "Stale.", details: [] }),
        });
        return;
      }
      await route.continue();
    });

    await page.getByTestId("draft-title-input").fill("Keep this exact text");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-keep-mine").click();

    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue("Keep this exact text");
  });

  test("double-clicking Save does not fire a second concurrent request", async ({ page }) => {
    await openSeededDraftEditor(page);

    let patchCount = 0;
    let releaseFirst: (() => void) | null = null;
    const firstRequestStarted = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });

    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      if (route.request().method() !== "PATCH") {
        await route.continue();
        return;
      }
      patchCount += 1;
      releaseFirst?.();
      // Hold the response open briefly so a second click, if it fired a
      // second request, would land while the first is still in flight.
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.continue();
    });

    await page.getByTestId("draft-title-input").fill("Double-click guard check");
    const saveButton = visibleTestId(page, "save-draft");
    await saveButton.click();
    await firstRequestStarted;
    await saveButton.click({ force: true });

    await page.waitForTimeout(700);
    expect(patchCount).toBe(1);
  });

  test("no console errors during the save/conflict/reload cycle", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await openSeededDraftEditor(page);

    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      const request = route.request();
      if (request.method() === "PATCH") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ code: "conflict", message: "Stale.", details: [] }),
        });
        return;
      }
      await route.continue();
    });

    await page.getByTestId("draft-title-input").fill("Console-error check");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-reload-latest").click();
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);

    const unexpected = errors.filter(
      (text) =>
        // Documented expected refresh probe, see other specs.
        !/401|Unauthorized/i.test(text) &&
        // Chrome logs any non-2xx XHR/fetch response to the console as
        // "Failed to load resource" regardless of whether the app handled
        // it -- this is the one intentionally-triggered 409 this test
        // itself causes above, already asserted on via the conflict
        // banner, not a genuine unhandled error.
        !/409 \(Conflict\)/i.test(text),
    );
    expect(unexpected).toEqual([]);
  });
});
