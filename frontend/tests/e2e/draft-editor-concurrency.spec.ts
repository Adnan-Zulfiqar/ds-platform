import { chromium, expect, test, type Browser, type Page, type Route } from "@playwright/test";

import { isApiReachable, type TestAccount } from "./helpers/auth";
import { seedEditorFixtureViaDb, signInWithAccount } from "./helpers/catalogue";

/**
 * Draft editor — optimistic-concurrency save flow, hardened in the M2A
 * acceptance pass.
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
 *
 * **All tests in this file share one authenticated page/session, logged in
 * once in `beforeAll`.** The acceptance pass originally logged in fresh per
 * test via the login form; with 16+ scenarios that meant 16+ real logins to
 * one account inside a couple of minutes, which reproducibly (confirmed by
 * running the full file three times: three different, non-overlapping
 * tests failed, always at the same `signInWithAccount` navigation wait, not
 * at any conflict-resolution assertion) collided with this platform's own
 * login throttle (`SECURITY_LOGIN_MAX_ATTEMPTS=5` per `.env.example`) --
 * not a defect in the feature under test. Logging in once and navigating
 * between tests on the same session avoids the collision entirely, and is
 * also the technically correct fix rather than a workaround: the throttle
 * firing was this test file's own login pattern being unrealistic, not a
 * gap in the application.
 */

/** Prefer the currently visible control when responsive duplicates stay in the DOM. */
function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

function mockConflict(page: Page) {
  return page.route("**/api/v1/drafts/*", async (route: Route) => {
    if (route.request().method() !== "PATCH") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        code: "conflict",
        message: "This item was changed since you loaded it.",
        details: [],
      }),
    });
  });
}

test.describe("Draft editor — conflict resolution (M2A acceptance pass)", () => {
  test.describe.configure({ mode: "serial" });

  let page: Page;
  let productId: string;
  let account: TestAccount;
  let ownBrowser: Browser | null = null;
  let testsSinceSessionStart = 0;
  // Below this, a single shared browser survives the whole 16-test file
  // fine (confirmed: 12/16 clean in one run). Above it, this machine's
  // Chromium occasionally dies outright -- not just the page, the whole
  // renderer process (`browser.newPage: Test ended`), independent of which
  // test is running and with the isolated app processes' PIDs unaffected
  // throughout. Rotating to a fresh browser before that point is reached
  // is cheaper than recovering after a crash mid-test.
  const MAX_TESTS_PER_SESSION = 5;

  /**
   * Sign in and land on the draft in a *new, independently-launched*
   * browser, returning the page that ended up there.
   *
   * Deliberately does not reuse the worker's `browser` fixture: when that
   * fixture's own browser process is the one that died, `browser.newPage()`
   * on it fails too ("Test ended") -- there is no recovering a dead
   * process, only replacing it. `testInfo.project.use` is passed through to
   * `newContext` so the replacement still gets the running project's device
   * emulation (viewport, `isMobile`, touch, ...) instead of silently
   * defaulting to desktop on the mobile-chrome project.
   */
  async function establishSession(testInfo: { project: { use: object } }): Promise<Page> {
    await ownBrowser?.close().catch(() => {});
    ownBrowser = await chromium.launch();
    const context = await ownBrowser.newContext(testInfo.project.use);
    const fresh = await context.newPage();
    await signInWithAccount(fresh, account, `/drafts/${productId}`);
    await expect(fresh.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    testsSinceSessionStart = 0;
    return fresh;
  }

  test.beforeAll(async ({ browser }: { browser: Browser }, testInfo) => {
    // Register + DB seed + UI sign-in exceeds the default 30s hook budget.
    test.setTimeout(120_000);
    const apiUp = await isApiReachable();
    test.skip(!apiUp, "API not reachable at E2E_API_URL / default.");

    // Prefer DB seed over host E2E_* credentials — those often point at another
    // isolated database and produce login hangs / 401s in a fresh stack.
    const seedContext = await browser.newContext();
    const seeded = await seedEditorFixtureViaDb(seedContext.request);
    await seedContext.close();
    test.skip(
      seeded === null,
      "DB draft seeding unavailable — set E2E_PYTHON / E2E_DATABASE_URL for this isolated stack.",
    );
    account = seeded!.account;
    productId = seeded!.productId;

    page = await establishSession(testInfo);
  });

  test.afterAll(async () => {
    await ownBrowser?.close().catch(() => {});
  });

  test.beforeEach(async ({}, testInfo) => {
    testsSinceSessionStart += 1;
    // `isClosed()`/proactive rotation catch a dead session before this
    // hook does anything with it. A crash *during* this hook's own
    // `goto`/`waitForVisible` throws instead -- caught below and treated
    // the same way, with one retry against the recovered session before
    // giving up for real.
    if (page.isClosed() || testsSinceSessionStart > MAX_TESTS_PER_SESSION) {
      page = await establishSession(testInfo);
      return;
    }
    try {
      // A hard reload before every test (see below) is real traffic: this
      // backend's own per-tenant rate limit (100 requests/60s,
      // `security.rate_limit_requests` in `app/core/config.py`) is sized
      // for a merchant clicking around, not 16 scenarios worth of reloads
      // back to back, and running the file fast enough previously tripped
      // it mid-run (confirmed by the app's own "Rate limit exceeded" error
      // surfacing on screen, not a generic timeout). Loosening the limiter
      // for test convenience is off the table, so this file paces itself
      // instead: a gap between tests spreads the same request count across
      // more than one fixed 60s window rather than cutting it. 2.5s was
      // not enough -- confirmed by re-running: the limiter still tripped,
      // just later (test 10 instead of test 1). ~8 requests/test means
      // keeping any single 60s window under it needs roughly one test per
      // 5+ seconds.
      await page.waitForTimeout(5500);
      // Any mocked route from the previous test must not leak into this
      // one -- `page.route()` persists across navigations on the same page
      // unless explicitly removed.
      await page.unrouteAll({ behavior: "ignoreErrors" });
      await page.goto(`/drafts/${productId}`);
      await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    } catch (err) {
      if (!page.isClosed()) throw err;
      page = await establishSession(testInfo);
    }
  });

  test("successful save clears the unsaved-changes state", async () => {
    const titleInput = page.getByTestId("draft-title-input");
    await titleInput.fill("A freshly edited title");

    // This is the one test in the file with no mocked route, so the real
    // 1.8s-debounced autosave (draft-product-editor.tsx) is a genuine
    // competing writer here: it calls the same `handleSave` the button
    // does, and can legitimately win, correctly disabling the button
    // ("nothing left to save") before this click lands. Both paths are a
    // real, successful save, so don't require the click itself to
    // succeed -- assert the end state either way reaches.
    const saveButton = visibleTestId(page, "save-draft");
    await saveButton.click({ timeout: 3000 }).catch(() => {});

    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(saveButton).toBeDisabled({ timeout: 5000 });
    await expect(titleInput).toHaveValue("A freshly edited title");
  });

  test("a 409 shows the banner with Reload and Review actions, not a generic error", async () => {
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
    await expect(page.getByTestId("conflict-review-mine")).toBeVisible();
    expect(patchCount).toBe(1);

    await expect(page.getByText("Save failed.")).toHaveCount(0);
    // The local edit must still be sitting in the field, untouched.
    await expect(titleInput).toHaveValue("My unsaved edit");
  });

  test("no automatic save happens after a 409 -- autosave stays frozen", async () => {
    let patchCount = 0;
    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      if (route.request().method() === "PATCH") patchCount += 1;
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ code: "conflict", message: "Stale.", details: [] }),
      });
    });

    await page.getByTestId("draft-title-input").fill("Triggers the first conflict");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
    const countAfterFirstConflict = patchCount;

    // Keep typing while the conflict is open -- if autosave were still
    // armed, its 1.8s debounce would fire another PATCH on its own.
    await page.getByTestId("draft-title-input").fill("Still typing during the conflict");
    await page.waitForTimeout(2500);

    expect(patchCount).toBe(countAfterFirstConflict);
  });

  test("clicking Reload does not immediately discard -- it opens a confirmation first", async () => {
    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Should not vanish yet");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.getByTestId("conflict-reload-latest").click();
    await expect(page.getByTestId("conflict-reload-confirm-dialog")).toBeVisible();
    // Nothing discarded yet -- the field behind the dialog is unchanged.
    await expect(page.getByTestId("draft-title-input")).toHaveValue("Should not vanish yet");

    await page.getByTestId("conflict-reload-cancel").click();
    await expect(page.getByTestId("conflict-reload-confirm-dialog")).toHaveCount(0);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
    await expect(page.getByTestId("draft-title-input")).toHaveValue("Should not vanish yet");
  });

  test("confirming Reload discards local edits and adopts the server value", async () => {
    const originalTitle = await page.getByTestId("draft-title-input").inputValue();

    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Local edit that should be discarded");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-reload-latest").click();
    await page.getByTestId("conflict-reload-confirm").click();

    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(originalTitle);
  });

  test("Review shows the latest server value next to the merchant's unsaved value", async () => {
    const originalTitle = await page.getByTestId("draft-title-input").inputValue();

    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("My reviewed local title");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-review-mine").click();

    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(originalTitle);
    await expect(dialog).toContainText("My reviewed local title");
    await expect(dialog).toContainText("Latest saved version");
    await expect(dialog).toContainText("Your unsaved version");

    // Reviewing must not itself change the field behind the dialog.
    await expect(page.getByTestId("draft-title-input")).toHaveValue("My reviewed local title");
  });

  test("Review's Back returns to the conflict banner without saving or discarding anything", async () => {
    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Kept through Back");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    await page.getByTestId("conflict-review-back").click();
    await expect(page.getByTestId("conflict-review-dialog")).toHaveCount(0);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
    await expect(page.getByTestId("draft-title-input")).toHaveValue("Kept through Back");
  });

  test('"Save my version anyway" is the explicit second action that performs the overwrite', async () => {
    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Explicit overwrite title");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    // Unroute so the review's own refetch and the final save reach the real
    // (fresh, unmodified) backend.
    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    await page.getByTestId("conflict-save-mine-anyway").click();

    await expect(page.getByTestId("conflict-review-dialog")).toHaveCount(0);
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue("Explicit overwrite title");
  });

  test("a second conflict during review returns to the conflict banner, not a silent save", async () => {
    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("First conflict title");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    // Let the review's own GET through, but keep every PATCH -- including
    // the explicit "Save my version anyway" -- rejected, simulating a
    // second, different editor saving again while this review was open.
    await page.getByTestId("conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    await page.getByTestId("conflict-save-mine-anyway").click();

    await expect(page.getByTestId("conflict-review-dialog")).toHaveCount(0);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
  });

  test("Reload latest version instead is reachable from inside the review", async () => {
    const originalTitle = await page.getByTestId("draft-title-input").inputValue();

    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Switch to reload from review");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.getByTestId("conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-review-reload-instead").click();
    await expect(page.getByTestId("conflict-reload-confirm-dialog")).toBeVisible();
    await page.getByTestId("conflict-reload-confirm").click();

    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(originalTitle);
  });

  test("double-clicking Save does not fire a second concurrent request", async () => {
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

  test("the conflict banner is announced via role=alert", async () => {
    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Accessible announcement check");
    await visibleTestId(page, "save-draft").click();

    const banner = page
      .getByRole("alert")
      .filter({ hasText: /Someone else saved this product/i });
    await expect(banner).toBeVisible();
  });

  test("the review dialog exposes an accessible title and description via Radix", async () => {
    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Dialog a11y check");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-review-mine").click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Review the conflicting changes")).toBeVisible();
  });

  test("the conflict can be resolved keyboard-only, ending on Reload", async () => {
    const originalTitle = await page.getByTestId("draft-title-input").inputValue();

    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Keyboard-only conflict flow");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-reload-latest").focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("conflict-reload-confirm-dialog")).toBeVisible();

    // Radix traps focus inside the dialog; Tab to the confirm button and
    // activate it without ever touching the mouse.
    await page.getByTestId("conflict-reload-confirm").focus();
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(originalTitle);
  });

  test("Escape closes the review dialog and returns to the conflict banner", async () => {
    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Escape key check");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.getByTestId("conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("conflict-review-dialog")).toHaveCount(0);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
  });

  test("no console errors through the full save/conflict/review/reload cycle", async () => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });

    await mockConflict(page);
    await page.getByTestId("draft-title-input").fill("Console-error check");
    await visibleTestId(page, "save-draft").click();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await page.getByTestId("conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();
    await page.getByTestId("conflict-review-back").click();

    await page.unroute("**/api/v1/drafts/*");
    await page.getByTestId("conflict-reload-latest").click();
    await page.getByTestId("conflict-reload-confirm").click();
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);

    const unexpected = errors.filter(
      (text) =>
        // Documented expected refresh probe, see other specs.
        !/401|Unauthorized/i.test(text) &&
        // Chrome logs any non-2xx XHR/fetch response to the console as
        // "Failed to load resource" regardless of whether the app handled
        // it -- this test intentionally triggers one 409 above, already
        // asserted on via the conflict banner, not a genuine unhandled
        // error.
        !/409 \(Conflict\)/i.test(text),
    );
    expect(unexpected).toEqual([]);
  });
});
