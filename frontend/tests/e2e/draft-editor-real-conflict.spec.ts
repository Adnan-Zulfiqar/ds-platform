import {
  chromium,
  expect,
  test,
  type APIRequestContext,
  type Browser,
  type Page,
} from "@playwright/test";

import { API_URL, isApiReachable, type TestAccount } from "./helpers/auth";
import { seedEditorFixtureViaDb, signInWithAccount } from "./helpers/catalogue";

/**
 * Draft editor — conflict resolution against a *genuinely* changed server row.
 *
 * Why this file exists separately from `draft-editor-concurrency.spec.ts`:
 *
 * That file produces its 409 by intercepting `PATCH /drafts/{id}` and
 * fulfilling a synthetic conflict. The underlying row therefore never
 * changes, so when the editor refetches the draft, React Query's structural
 * sharing hands back a *reference-identical* `data` object. Every code path
 * that reacts to `data` changing is silently skipped, and a whole class of
 * defect becomes invisible to that suite -- which is exactly what happened:
 * "Review my changes" refetched, the new `data` reference drove the form
 * hydration effect, and the merchant's unsaved work was destroyed. All 32
 * mocked tests passed against the build that did it.
 *
 * Here nothing is mocked. A second, independent API client saves a real new
 * version between the editor's load and its save, so:
 *   - the 409 is produced by the backend's own compare-and-swap;
 *   - the refetched draft really is materially different;
 *   - React Query really does produce a new `data` reference.
 *
 * Structural sharing is a rendering optimisation. It is not a safety
 * property, and a test suite that depends on it to stay green is testing
 * the cache, not the feature.
 */

/** Prefer the currently visible control when responsive duplicates stay in the DOM. */
function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

/**
 * Make a value unique to this run.
 *
 * These tests are routinely pointed at one long-lived pre-seeded draft
 * (`E2E_PRODUCT_ID`), so a hard-coded title can already be the row's current
 * value when a test starts — left there by an earlier run, or by the same
 * test on another project. Two things then break, both silently and both
 * misleadingly: the backend correctly treats an identical write as a no-op
 * and does *not* advance `updatedAt` (so no conflict can be armed), and
 * `fill()` with the value already present fires no change event (so the
 * form never becomes dirty). Neither is a product defect, but both surface
 * as one. A per-run suffix removes the whole class.
 */
let uniqueCounter = 0;
function uniq(label: string): string {
  uniqueCounter += 1;
  return `${label}-${Date.now().toString(36)}-${uniqueCounter}`;
}

interface LoginResponse {
  tokens: { accessToken: string };
}

interface DraftResponse {
  id: string;
  title: string;
  updatedAt: string;
}

/** A bearer token for an API client that is entirely independent of the
 * browser session under test -- this is the "other editor". */
async function loginViaApi(
  request: APIRequestContext,
  account: TestAccount,
): Promise<string> {
  const response = await request.post(`${API_URL}/api/v1/auth/login`, {
    data: { email: account.email, password: account.password },
  });
  expect(response.ok(), `login for the independent writer failed: ${response.status()}`).toBe(
    true,
  );
  const body = (await response.json()) as LoginResponse;
  return body.tokens.accessToken;
}

async function readDraft(
  request: APIRequestContext,
  token: string,
  productId: string,
): Promise<DraftResponse> {
  const response = await request.get(`${API_URL}/api/v1/drafts/${productId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok(), `reading the draft failed: ${response.status()}`).toBe(true);
  return (await response.json()) as DraftResponse;
}

/**
 * The other editor saves. Reads the current version token first so this
 * write always succeeds, then returns the new one -- which is precisely the
 * value that makes whatever the browser is holding stale.
 */
async function otherEditorSaves(
  request: APIRequestContext,
  token: string,
  productId: string,
  title: string,
): Promise<string> {
  const current = await readDraft(request, token, productId);
  const response = await request.patch(`${API_URL}/api/v1/drafts/${productId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { title, expectedUpdatedAt: current.updatedAt },
  });
  expect(
    response.ok(),
    `the independent writer's own save should succeed: ${response.status()} ${await response.text()}`,
  ).toBe(true);
  const body = (await response.json()) as DraftResponse;
  expect(body.updatedAt).not.toBe(current.updatedAt);
  return body.updatedAt;
}

test.describe("Draft editor — real server-side conflict", () => {
  test.describe.configure({ mode: "serial" });

  let page: Page;
  let productId: string;
  let account: TestAccount;
  let writerToken: string;
  let ownBrowser: Browser | null = null;
  let consoleErrors: string[] = [];

  /** Errors this flow is *supposed* to produce: the rejected save really is
   * a 409, and Chromium logs every non-2xx as a console error. Anything
   * else is a genuine unexpected error (requirement 24). */
  const EXPECTED_CONSOLE_ERROR = /\b(409|401)\b|Failed to load resource/i;

  async function establishSession(testInfo: { project: { use: object } }) {
    await ownBrowser?.close().catch(() => {});
    ownBrowser = await chromium.launch();
    const context = await ownBrowser.newContext(testInfo.project.use);
    const fresh = await context.newPage();
    fresh.on("console", (message) => {
      if (message.type() === "error" && !EXPECTED_CONSOLE_ERROR.test(message.text())) {
        consoleErrors.push(message.text());
      }
    });
    await signInWithAccount(fresh, account, `/drafts/${productId}`);
    await expect(fresh.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
    return fresh;
  }

  test.beforeAll(async ({ browser }: { browser: Browser }, testInfo) => {
    test.setTimeout(120_000);
    test.skip(!(await isApiReachable()), "API not reachable at NEXT_PUBLIC_API_URL / default.");

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

  test.beforeEach(async () => {
    consoleErrors = [];
    // The same self-pacing this platform's own per-tenant rate limit forces
    // on `draft-editor-concurrency.spec.ts` (100 requests/60s, see
    // `security.rate_limit_requests`): a hard reload per test is real
    // traffic, and these tests add the independent writer's calls on top.
    await page.waitForTimeout(5500);
    writerToken = await loginViaApi(page.request, account);
    await page.goto(`/drafts/${productId}`);
    await expect(page.getByTestId("draft-editor")).toBeVisible({ timeout: 30_000 });
  });

  /**
   * Put the editor into a real conflict.
   *
   * Order matters and is deliberate: the other editor saves *first*, so the
   * token this browser is holding is already stale by the time autosave
   * fires. Typing first would race -- autosave could win, succeed, and
   * refresh the token before the other write landed.
   */
  async function arriveAtRealConflict(localTitle: string, serverTitle: string) {
    const serverVersion = await otherEditorSaves(
      page.request,
      writerToken,
      productId,
      serverTitle,
    );
    await page.getByTestId("draft-title-input").fill(localTitle);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible({
      timeout: 20_000,
    });
    return serverVersion;
  }

  test("a real 409 preserves the merchant's edits and never reports success", async () => {
    const local = "Local title the merchant typed";
    await arriveAtRealConflict(local, "Server title from the other editor");

    // Requirements 5, 6: the banner is up and the merchant's text survived
    // a save that the backend genuinely rejected.
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);
    await expect(page.getByTestId("conflict-reload-latest")).toBeVisible();
    await expect(page.getByTestId("conflict-review-mine")).toBeVisible();

    // Requirement 12: unresolved local edits must never read as saved.
    await expect(page.getByTestId("draft-save-state")).not.toHaveText(
      /All changes saved/i,
    );
    await expect(page.getByText("Save failed.")).toHaveCount(0);
  });

  test("opening review shows both versions and leaves local input untouched", async () => {
    const local = "My unsaved local title";
    const server = "A materially different server title";
    await arriveAtRealConflict(local, server);

    let patchCount = 0;
    page.on("request", (request) => {
      if (request.method() === "PATCH" && request.url().includes("/drafts/")) {
        patchCount += 1;
      }
    });

    await visibleTestId(page, "conflict-review-mine").click();

    // Requirements 10, 11: review is open and shows both sides.
    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(server)).toBeVisible();
    await expect(dialog.getByText(local)).toBeVisible();

    // Requirement 9 -- the whole point of this file. The refetch behind
    // review returns genuinely different bytes, so React Query yields a new
    // `data` reference; the form must not follow it.
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);

    // Requirement 12.
    await expect(page.getByTestId("draft-save-state")).not.toHaveText(
      /All changes saved/i,
    );

    // Requirement 13: reviewing is inert. Well past the 1.8s autosave debounce.
    await page.waitForTimeout(3000);
    expect(patchCount).toBe(0);
    await expect(dialog).toBeVisible();
  });

  test("closing review keeps the local values and the unresolved conflict", async () => {
    const local = "Still mine after closing review";
    await arriveAtRealConflict(local, "Server value behind the review");

    await visibleTestId(page, "conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    await page.getByTestId("conflict-review-back").click();

    // Requirement 14.
    await expect(page.getByTestId("conflict-review-dialog")).toBeHidden();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);
  });

  test("Save my version anyway sends one PATCH on the reviewed token and persists", async () => {
    const local = "The version the merchant insists on";
    await arriveAtRealConflict(local, "Server value about to be overwritten");

    await visibleTestId(page, "conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    const patchBodies: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "PATCH" && request.url().includes("/drafts/")) {
        patchBodies.push(request.postData() ?? "");
      }
    });

    await page.getByTestId("conflict-save-mine-anyway").click();

    // Requirement 15: exactly one guarded request, asserting the *reviewed*
    // server token -- not the stale one the rejected save used.
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    expect(patchBodies).toHaveLength(1);
    expect(patchBodies[0]).toContain("expectedUpdatedAt");

    // Requirement 16: the merchant's values really are what persisted.
    const persisted = await readDraft(page.request, writerToken, productId);
    expect(persisted.title).toBe(local);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);
  });

  test("a second real change during review produces another 409 and still keeps local values", async () => {
    const local = "Merchant value that survives twice";
    await arriveAtRealConflict(local, "First server value");

    await visibleTestId(page, "conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    // Requirement 17: someone else saves *again*, after the review opened,
    // so the token the override is about to assert is itself now stale.
    await otherEditorSaves(page.request, writerToken, productId, "Second server value");

    await page.getByTestId("conflict-save-mine-anyway").click();

    // Requirement 18: back to an unresolved conflict, merchant's work intact.
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);
    await expect(page.getByTestId("draft-save-state")).not.toHaveText(
      /All changes saved/i,
    );

    // And the second review reflects the *newer* server value, not the first.
    await visibleTestId(page, "conflict-review-mine").click();
    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("Second server value")).toBeVisible();
  });

  test("reload-latest cancel keeps local edits; confirming adopts the server version", async () => {
    const local = "Local edit pending a reload decision";
    const server = "Server version that reload should adopt";
    await arriveAtRealConflict(local, server);

    await visibleTestId(page, "conflict-reload-latest").click();
    await expect(page.getByTestId("conflict-reload-confirm-dialog")).toBeVisible();
    await page.getByTestId("conflict-reload-cancel").click();

    // Requirement 19.
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await visibleTestId(page, "conflict-reload-latest").click();
    await page.getByTestId("conflict-reload-confirm").click();

    // Requirement 20: this is the one path that may discard local work.
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(server);
  });

  test("a background query refetch during a conflict does not hydrate the form", async () => {
    const local = "Untouched by a background refetch";
    const server = "Server value the refetch will deliver";
    await arriveAtRealConflict(local, server);

    // Requirement 21. Uses the non-production QueryClient handle the app
    // already exposes for E2E probes (`providers/query-provider.tsx`) --
    // this is the real mechanism `useUpdateDraft` uses after every save,
    // not a synthetic one invented for the test.
    const invalidated = await page.evaluate(async (id) => {
      const handle = window as unknown as {
        __DROPPLOT_QUERY_CLIENT__?: {
          invalidateQueries: (filters: { queryKey: unknown[] }) => Promise<void>;
        };
      };
      if (!handle.__DROPPLOT_QUERY_CLIENT__) return false;
      await handle.__DROPPLOT_QUERY_CLIENT__.invalidateQueries({
        queryKey: ["drafts", "detail", id],
      });
      return true;
    }, productId);
    expect(invalidated, "the E2E QueryClient handle should be exposed").toBe(true);

    await page.waitForTimeout(1500);

    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
    await expect(page.getByTestId("draft-save-state")).not.toHaveText(
      /All changes saved/i,
    );
  });

  test("conflict resolution is reachable and operable by keyboard alone", async () => {
    const local = "Resolved without a mouse";
    await arriveAtRealConflict(local, "Server value for the keyboard run");

    // Requirement 22: reach Review from the banner using only the keyboard,
    // and confirm focus is actually visible on the way (not a focus trap or
    // an invisible outline).
    const review = visibleTestId(page, "conflict-review-mine");
    await review.focus();
    await expect(review).toBeFocused();
    await page.keyboard.press("Enter");

    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);
  });

  test("the conflict flow fits a 375px viewport without horizontal overflow", async () => {
    await page.setViewportSize({ width: 375, height: 812 });
    const local = "Mobile conflict value";
    await arriveAtRealConflict(local, "Server value on mobile");

    // Requirement 23.
    const overflowAtBanner = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflowAtBanner).toBe(false);

    await visibleTestId(page, "conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();
    await expect(page.getByTestId("draft-title-input")).toHaveValue(local);

    const overflowInReview = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflowInReview).toBe(false);

    await page.setViewportSize({ width: 1280, height: 800 });
  });

  /**
   * The consent contract: what the review dialog shows is what gets saved.
   *
   * These cover the defect the V2 integration pass found. Editing stays
   * enabled during a conflict (only saving is frozen), so a merchant can
   * type after the 409. The dialog used to render the values frozen at the
   * 409 while the override rebuilt its payload from live form state, so the
   * screen said one thing and the database got another. Nothing in the
   * previous suite typed after the conflict, so nothing caught it.
   */

  test("review shows what was typed after the conflict, and saves exactly that", async () => {
    const atConflict = uniq("LOCAL-A-at-conflict");
    const afterConflict = uniq("LOCAL-B-typed-after-conflict");
    const server = uniq("SERVER-V2-VALUE");
    await arriveAtRealConflict(atConflict, server);

    // The merchant keeps working while the banner is up -- legitimate, and
    // the whole reason the 409-time snapshot must not be what gets saved.
    await page.getByTestId("draft-title-input").fill(afterConflict);

    const patchBodies: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "PATCH" && request.url().includes("/drafts/")) {
        patchBodies.push(request.postData() ?? "");
      }
    });

    await visibleTestId(page, "conflict-review-mine").click();
    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();

    // The dialog must show the *current* intent, not the rejected one.
    await expect(dialog.getByText(afterConflict)).toBeVisible();
    await expect(dialog.getByText(atConflict)).toHaveCount(0);
    await expect(dialog.getByText(server)).toBeVisible();

    // The difference count is computed from the same reviewed pair.
    await expect(dialog).toContainText("1 field differs");

    // Opening a comparison is not a write.
    expect(patchBodies).toHaveLength(0);

    await page.getByTestId("conflict-save-mine-anyway").click();
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);

    // Dialog == payload == database == form baseline.
    expect(patchBodies).toHaveLength(1);
    expect(patchBodies[0]).toContain(afterConflict);
    expect(patchBodies[0]).not.toContain(atConflict);

    const persisted = await readDraft(page.request, writerToken, productId);
    expect(persisted.title).toBe(afterConflict);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(afterConflict);
    await expect(page.getByTestId("draft-save-state")).not.toHaveText(/Unsaved changes/i);
  });

  test("closing review and typing again re-captures on reopen, and saves the newest text", async () => {
    const afterConflict = "LOCAL-B-typed-after-conflict";
    const afterClosing = uniq("LOCAL-C-after-closing-review");
    await arriveAtRealConflict(uniq("LOCAL-A-at-conflict"), uniq("SERVER-value-for-C-run"));

    await page.getByTestId("draft-title-input").fill(afterConflict);
    await visibleTestId(page, "conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    // Walk away from the comparison, then keep editing.
    await page.getByTestId("conflict-review-back").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeHidden();
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();
    await page.getByTestId("draft-title-input").fill(afterClosing);

    // Reopening must re-capture, not resurrect the earlier comparison.
    await visibleTestId(page, "conflict-review-mine").click();
    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(afterClosing)).toBeVisible();
    await expect(dialog.getByText(afterConflict)).toHaveCount(0);

    await page.getByTestId("conflict-save-mine-anyway").click();
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);

    const persisted = await readDraft(page.request, writerToken, productId);
    expect(persisted.title).toBe(afterClosing);
  });

  test("a form change after review opens blocks the save and demands a fresh comparison", async () => {
    const reviewed = uniq("LOCAL-reviewed-value");
    const sneaked = uniq("LOCAL-changed-behind-the-dialog");
    await arriveAtRealConflict(reviewed, uniq("SERVER-value-for-guard-run"));

    await visibleTestId(page, "conflict-review-mine").click();
    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(reviewed)).toBeVisible();

    // Change the field *behind* the open dialog, the way an autofill or a
    // stray script would. The dialog still shows the reviewed value.
    await page.evaluate((value) => {
      const el = document.querySelector<HTMLInputElement>(
        '[data-testid="draft-title-input"]',
      );
      if (!el) throw new Error("title input missing");
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    }, sneaked);

    const patchBodies: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "PATCH" && request.url().includes("/drafts/")) {
        patchBodies.push(request.postData() ?? "");
      }
    });

    await page.getByTestId("conflict-save-mine-anyway").click();

    // Refused: nothing written, and the merchant is told why.
    expect(patchBodies).toHaveLength(0);
    await expect(page.getByTestId("conflict-review-stale")).toBeVisible();
    await expect(page.getByTestId("conflict-save-mine-anyway")).toBeDisabled();
    const untouched = await readDraft(page.request, writerToken, productId);
    expect(untouched.title).not.toBe(sneaked);
    expect(untouched.title).not.toBe(reviewed);

    // Refreshing shows the new values and re-enables the override.
    await page.getByTestId("conflict-review-refresh").click();
    await expect(page.getByTestId("conflict-review-stale")).toHaveCount(0);
    await expect(dialog.getByText(sneaked)).toBeVisible();
    await expect(page.getByTestId("conflict-save-mine-anyway")).toBeEnabled();

    await page.getByTestId("conflict-save-mine-anyway").click();
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    expect(patchBodies).toHaveLength(1);
    expect(patchBodies[0]).toContain(sneaked);

    const persisted = await readDraft(page.request, writerToken, productId);
    expect(persisted.title).toBe(sneaked);
  });

  test("a second real conflict preserves the reviewed merchant version", async () => {
    const mine = uniq("LOCAL-survives-second-conflict");
    await arriveAtRealConflict(mine, uniq("SERVER-first-for-second-conflict"));

    await visibleTestId(page, "conflict-review-mine").click();
    await expect(page.getByTestId("conflict-review-dialog")).toBeVisible();

    // Someone else saves again while the comparison is open.
    const secondServer = uniq("SERVER-second-for-second-conflict");
    await otherEditorSaves(
      page.request,
      writerToken,
      productId,
      secondServer,
    );

    await page.getByTestId("conflict-save-mine-anyway").click();

    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible({
      timeout: 20_000,
    });
    await expect(page.getByTestId("draft-title-input")).toHaveValue(mine);

    // The next review reflects the newer server value and still the
    // merchant's own text.
    await visibleTestId(page, "conflict-review-mine").click();
    const dialog = page.getByTestId("conflict-review-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(secondServer)).toBeVisible();
    await expect(dialog.getByText(mine)).toBeVisible();
  });

  test("reload-latest still cancels safely and confirms destructively after later typing", async () => {
    const newest = uniq("LOCAL-newest-before-reload-decision");
    const reloadServer = uniq("SERVER-value-reload-should-adopt");
    await arriveAtRealConflict(uniq("LOCAL-A-at-conflict"), reloadServer);

    // Type after the conflict, so Cancel must preserve the *newest* text.
    await page.getByTestId("draft-title-input").fill(newest);

    await visibleTestId(page, "conflict-reload-latest").click();
    await expect(page.getByTestId("conflict-reload-confirm-dialog")).toBeVisible();
    await page.getByTestId("conflict-reload-cancel").click();
    await expect(page.getByTestId("draft-title-input")).toHaveValue(newest);
    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible();

    await visibleTestId(page, "conflict-reload-latest").click();
    await page.getByTestId("conflict-reload-confirm").click();
    await expect(page.getByTestId("draft-conflict-banner")).toHaveCount(0);
    await expect(page.getByTestId("draft-title-input")).toHaveValue(reloadServer);
  });

  test.afterEach(() => {
    // Requirement 24, asserted per test so a failure names the flow that
    // produced it rather than the last one to run.
    expect(consoleErrors, `unexpected console errors: ${consoleErrors.join(" | ")}`).toEqual(
      [],
    );
  });
});
