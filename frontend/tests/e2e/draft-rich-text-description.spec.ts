import {
  chromium,
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Browser,
  type Page,
  type Route,
} from "@playwright/test";

import { API_URL, isApiReachable, type TestAccount } from "./helpers/auth";
import { seedCatalogueViaApi, signInWithAccount } from "./helpers/catalogue";

/**
 * M2B — rich-text product description.
 *
 * Everything here runs against a real authenticated session and a real
 * seeded draft; only the two tests that need a specific server failure
 * intercept `PATCH /drafts/{id}`. That split is deliberate and was learned
 * the expensive way in M2A: a suite that mocks the save endpoint can never
 * observe what the backend actually stores, and the M2A acceptance pass
 * found a data-loss defect that 32 fully-mocked tests had passed over. The
 * formatting assertions below therefore round-trip through a genuine save
 * and reload rather than inspecting the DOM immediately after a click.
 *
 * Session handling (shared page, rotation, inter-test pacing) mirrors
 * `draft-editor-concurrency.spec.ts`; its header documents why each of
 * those measures exists — login throttling and this platform's own
 * per-tenant rate limit, neither of which is a defect in the feature under
 * test.
 */

/** Prefer the currently visible control when responsive duplicates stay in the DOM. */
function visibleTestId(page: Page, testId: string) {
  return page.locator(`[data-testid="${testId}"]:visible`);
}

/** Make a value unique to this run — see the same helper's rationale in
 * `draft-editor-real-conflict.spec.ts`: these tests are routinely pointed at
 * one long-lived pre-seeded draft, and re-submitting a value the row already
 * holds is correctly a backend no-op, which looks like a broken save. */
let uniqueCounter = 0;
function uniq(label: string): string {
  uniqueCounter += 1;
  return `${label}-${Date.now().toString(36)}-${uniqueCounter}`;
}

/** Replace the editor's whole document with `text`, leaving it focused. */
async function typeFresh(page: Page, text: string): Promise<void> {
  const editor = page.getByTestId("draft-description-editor");
  await editor.click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Delete");
  await page.keyboard.type(text);
}

/**
 * Fire a real `paste` at the editor's contenteditable node.
 *
 * ProseMirror's paste handling is the security-relevant path here — it is
 * what a merchant copying a competitor's product page actually triggers —
 * and it can only be reached by dispatching a genuine `ClipboardEvent` with
 * a populated `DataTransfer`. Writing to the system clipboard and pressing
 * Ctrl+V would need clipboard permissions that differ per platform.
 */
async function pasteHtml(page: Page, html: string): Promise<void> {
  const editor = page.getByTestId("draft-description-editor");
  await editor.click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.evaluate((markup: string) => {
    const node = document.querySelector('[data-testid="draft-description-editor"]');
    if (!node) throw new Error("editor node not found");
    const transfer = new DataTransfer();
    transfer.setData("text/html", markup);
    transfer.setData("text/plain", markup.replace(/<[^>]+>/g, ""));
    node.dispatchEvent(
      new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }),
    );
  }, html);
}

/** A supplier description in the shape that actually breaks editors: an
 * image and a table, neither of which the toolbar can create and both of
 * which the sanitizer allows. */
const SUPPLIER_DESCRIPTION =
  "<p>Supplier copy.</p>" +
  '<img src="https://ae01.alicdn.com/kf/regression-one.jpg" alt="Supplier photo">' +
  "<table><tr><th>Size</th><td>Large</td></tr></table>" +
  "<h1>Legacy heading</h1>";

/** Read a draft through the API, independently of the browser session. */
async function readDraftViaApi(
  api: APIRequestContext,
  token: string,
  productId: string,
): Promise<{ updatedAt: string; description: string | null }> {
  const response = await api.get(`${API_URL}/api/v1/drafts/${productId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok(), `reading the draft failed: ${response.status()}`).toBe(true);
  return (await response.json()) as { updatedAt: string; description: string | null };
}

/**
 * Put a known description on the draft through the API.
 *
 * The spec seeds its own fixture rather than trusting whatever the row
 * happens to hold: these tests are routinely pointed at one long-lived
 * draft, and every test below that saves a description overwrites it for
 * the next run. A regression test whose input depends on the previous run
 * silently stops testing anything.
 */
async function setDescriptionViaApi(
  api: APIRequestContext,
  token: string,
  productId: string,
  html: string,
): Promise<void> {
  const { updatedAt } = await readDraftViaApi(api, token, productId);
  const response = await api.patch(`${API_URL}/api/v1/drafts/${productId}`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { description: html, expectedUpdatedAt: updatedAt },
  });
  expect(response.ok(), `seeding the description failed: ${response.status()}`).toBe(true);
}

/** Number of `PATCH /drafts/{id}` requests currently in flight on the page
 * under test. Maintained by {@link trackDraftWrites}. */
let pendingWrites = 0;

function isDraftWrite(request: { method(): string; url(): string }): boolean {
  return request.method() === "PATCH" && request.url().includes("/api/v1/drafts/");
}

/** Count draft writes in flight, so a test can wait for one to *land*. */
function trackDraftWrites(target: Page): void {
  target.on("request", (r) => {
    if (isDraftWrite(r)) pendingWrites += 1;
  });
  target.on("requestfinished", (r) => {
    if (isDraftWrite(r)) pendingWrites -= 1;
  });
  target.on("requestfailed", (r) => {
    if (isDraftWrite(r)) pendingWrites -= 1;
  });
}

/**
 * Save, and wait until the write has actually reached the server.
 *
 * Waiting on the Save button alone is not enough, and getting this wrong
 * produced a false failure during this build: the button is disabled both
 * while a save is in flight *and* once there is nothing left to save, so
 * "disabled" cannot distinguish "saved" from "saving". A test that reads
 * the draft back through the API at that moment can beat its own PATCH and
 * see the pre-edit value — which looks exactly like the editor having
 * dropped the merchant's work.
 *
 * The 1.8s autosave is also a legitimate competing writer and can win the
 * race to save, correctly disabling the button before the click lands.
 * Both paths are a real save, so the click is allowed to fail; what is
 * asserted is that no write is still outstanding.
 */
async function saveAndSettle(page: Page): Promise<void> {
  const save = visibleTestId(page, "save-draft");
  await save.click({ timeout: 3000 }).catch(() => {});
  await expect(save).toBeDisabled({ timeout: 10_000 });
  await expect.poll(() => pendingWrites, { timeout: 15_000 }).toBe(0);
}

test.describe("Draft editor — rich-text description (M2B)", () => {
  test.describe.configure({ mode: "serial" });

  let page: Page;
  let productId: string;
  let account: TestAccount;
  let ownBrowser: Browser | null = null;
  let testsSinceSessionStart = 0;
  const MAX_TESTS_PER_SESSION = 5;

  // One API client, authenticated once. Used to seed fixtures and to read
  // back what was actually stored. Deliberately a single login for the whole
  // file: this platform throttles repeated logins per account
  // (`SECURITY_LOGIN_MAX_ATTEMPTS`), and a per-test login would trip it.
  let api: APIRequestContext;
  let apiToken: string;

  async function establishSession(testInfo: { project: { use: object } }): Promise<Page> {
    await ownBrowser?.close().catch(() => {});
    ownBrowser = await chromium.launch();
    const context = await ownBrowser.newContext(testInfo.project.use);
    const fresh = await context.newPage();
    trackDraftWrites(fresh);
    await signInWithAccount(fresh, account, `/drafts/${productId}?tab=description`);
    await expect(fresh.getByTestId("draft-description-editor")).toBeVisible({ timeout: 30_000 });
    testsSinceSessionStart = 0;
    return fresh;
  }

  test.beforeAll(async ({ browser }: { browser: Browser }, testInfo) => {
    const apiUp = await isApiReachable();
    test.skip(!apiUp, "API not reachable at E2E_API_URL / default.");

    const existingId = process.env.E2E_PRODUCT_ID;
    const existingEmail = process.env.E2E_EMAIL;
    const existingPassword = process.env.E2E_PASSWORD;

    if (existingId && existingEmail && existingPassword) {
      account = { email: existingEmail, password: existingPassword, companyName: "E2E Existing" };
      productId = existingId;
    } else {
      const seedContext = await browser.newContext();
      const seeded = await seedCatalogueViaApi(seedContext.request);
      await seedContext.close();
      test.skip(
        seeded === null,
        "Catalogue seeding failed — set E2E_PRODUCT_ID/E2E_EMAIL/E2E_PASSWORD or enable AliExpress import.",
      );
      account = seeded!.account;
      productId = seeded!.product.id;
    }

    api = await playwrightRequest.newContext();
    const login = await api.post(`${API_URL}/api/v1/auth/login`, {
      data: { email: account.email, password: account.password },
    });
    expect(login.ok(), `API login failed: ${login.status()}`).toBe(true);
    apiToken = ((await login.json()) as { tokens: { accessToken: string } }).tokens.accessToken;

    page = await establishSession(testInfo);
  });

  test.afterAll(async () => {
    await ownBrowser?.close().catch(() => {});
    await api?.dispose();
  });

  test.beforeEach(async ({}, testInfo) => {
    testsSinceSessionStart += 1;
    // A write left outstanding by the previous test would make the next
    // one's `saveAndSettle` wait forever; each test starts from zero.
    pendingWrites = 0;
    if (page.isClosed() || testsSinceSessionStart > MAX_TESTS_PER_SESSION) {
      page = await establishSession(testInfo);
      return;
    }
    try {
      await page.waitForTimeout(5500);
      await page.unrouteAll({ behavior: "ignoreErrors" });
      await page.goto(`/drafts/${productId}?tab=description`);
      await expect(page.getByTestId("draft-description-editor")).toBeVisible({ timeout: 30_000 });
    } catch (err) {
      if (!page.isClosed()) throw err;
      page = await establishSession(testInfo);
    }
  });

  test("the description tab is a rich-text surface, not an HTML source box", async () => {
    await expect(page.getByTestId("draft-description-editor")).toBeVisible();
    await expect(page.getByRole("toolbar", { name: "Text formatting" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Bold" })).toBeVisible();
    await expect(page.getByTestId("draft-description-length")).toBeVisible();

    // The raw-HTML textarea M2B replaced, and its separate preview pane,
    // must be gone rather than merely hidden — two editing surfaces for one
    // field is how the two diverge.
    await expect(page.getByTestId("draft-description-input")).toHaveCount(0);
    await expect(page.getByTestId("draft-description-preview")).toHaveCount(0);
  });

  test("loading a draft does not mark it unsaved", async () => {
    // The regression this guards, found live during the M2B build:
    // ProseMirror emits an update for its own normalisation as well as for
    // edits, the parent counted that as `dirty`, and autosave rewrote a
    // draft nobody had touched on every single page view — bumping
    // `updatedAt` and invalidating every other tab's version token.
    //
    // Seeded with markup the editor has to *normalise* (a table gains a
    // `<colgroup>`; a document ending in a table gains a trailing
    // paragraph), because a description that round-trips byte-identically
    // would pass this test on the broken build too.
    await setDescriptionViaApi(api, apiToken, productId, SUPPLIER_DESCRIPTION);

    let patches = 0;
    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      if (route.request().method() === "PATCH") patches += 1;
      await route.continue();
    });

    await page.reload();
    await expect(page.getByTestId("draft-description-editor")).toBeVisible({ timeout: 30_000 });
    await expect(visibleTestId(page, "save-draft")).toBeDisabled();
    // Comfortably past the 1.8s autosave debounce.
    await page.waitForTimeout(4000);

    expect(patches, "opening a draft must not write to it").toBe(0);
    await expect(visibleTestId(page, "save-draft")).toBeDisabled();
  });

  test("supplier images and tables survive being opened and edited", async () => {
    // The defect this guards, also found live: the editor's schema had no
    // image node, so ProseMirror discarded every `<img>` on parse and the
    // autosave persisted the stripped copy. Supplier descriptions are
    // mostly images — that is silent, permanent loss of the merchant's
    // product content, triggered by nothing more than opening the tab.
    //
    // M2B adds no way to *insert* an image. This asserts the different
    // guarantee: what is already there is not destroyed.
    await setDescriptionViaApi(api, apiToken, productId, SUPPLIER_DESCRIPTION);
    await page.reload();

    const editor = page.getByTestId("draft-description-editor");
    await expect(editor).toBeVisible({ timeout: 30_000 });
    await expect(editor.locator("img")).toHaveAttribute(
      "src",
      "https://ae01.alicdn.com/kf/regression-one.jpg",
    );
    await expect(editor.locator("table")).toBeVisible();
    await expect(editor.locator("h1")).toContainText("Legacy heading");

    // Now make a real edit and save: the image must still be in what the
    // server ends up storing, not merely in what the browser rendered.
    const marker = uniq("Merchant addition");
    // Click the first paragraph, not the editor box. Clicking the box hits
    // its centre, which in this fixture is the image — a `contenteditable
    // ="false"` node view. That selects the node instead of placing a text
    // cursor, and on a touch viewport the editable root never takes focus,
    // so the keystrokes went nowhere.
    await editor.locator("p").first().click();
    await page.keyboard.press("End");
    await page.keyboard.type(` ${marker}`);
    await saveAndSettle(page);

    const stored = await readDraftViaApi(api, apiToken, productId);
    expect(stored.description ?? "").toContain("https://ae01.alicdn.com/kf/regression-one.jpg");
    expect(stored.description ?? "").toContain(marker);
    expect(stored.description ?? "").toContain("<table");
  });

  test("bold and italic survive a save and a reload", async () => {
    const marker = uniq("Formatted");
    await typeFresh(page, marker);
    await page.keyboard.press("ControlOrMeta+a");
    await page.getByRole("button", { name: "Bold" }).click();
    await page.getByRole("button", { name: "Italic" }).click();

    const editor = page.getByTestId("draft-description-editor");
    await expect(editor.locator("strong")).toContainText(marker);
    await saveAndSettle(page);

    await page.reload();
    const reloaded = page.getByTestId("draft-description-editor");
    await expect(reloaded).toBeVisible({ timeout: 30_000 });
    await expect(reloaded.locator("strong")).toContainText(marker);
    await expect(reloaded.locator("em")).toContainText(marker);
  });

  test("a bulleted list and a heading survive a save and a reload", async () => {
    const heading = uniq("Spec");
    const item = uniq("Item");

    await typeFresh(page, heading);
    await page.getByRole("button", { name: "Heading 2" }).click();
    await page.getByTestId("draft-description-editor").click();
    await page.keyboard.press("ControlOrMeta+End");
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Bulleted list" }).click();
    await page.keyboard.type(item);

    await saveAndSettle(page);
    await page.reload();

    const reloaded = page.getByTestId("draft-description-editor");
    await expect(reloaded).toBeVisible({ timeout: 30_000 });
    await expect(reloaded.locator("h2")).toContainText(heading);
    await expect(reloaded.locator("ul li")).toContainText(item);
  });

  test("undo reverses the last formatting change and redo reapplies it", async () => {
    const marker = uniq("Undoable");
    await typeFresh(page, marker);
    await page.keyboard.press("ControlOrMeta+a");
    await page.getByRole("button", { name: "Bold" }).click();

    const editor = page.getByTestId("draft-description-editor");
    await expect(editor.locator("strong")).toHaveCount(1);

    await page.getByRole("button", { name: "Undo" }).click();
    await expect(editor.locator("strong")).toHaveCount(0);
    await expect(editor).toContainText(marker);

    await page.getByRole("button", { name: "Redo" }).click();
    await expect(editor.locator("strong")).toHaveCount(1);
  });

  test("clear formatting strips marks without deleting the text", async () => {
    const marker = uniq("Cleared");
    await typeFresh(page, marker);
    await page.keyboard.press("ControlOrMeta+a");
    await page.getByRole("button", { name: "Bold" }).click();
    await page.getByRole("button", { name: "Strikethrough" }).click();

    const editor = page.getByTestId("draft-description-editor");
    await expect(editor.locator("strong")).toHaveCount(1);

    await page.keyboard.press("ControlOrMeta+a");
    await page.getByRole("button", { name: "Clear formatting" }).click();

    await expect(editor.locator("strong")).toHaveCount(0);
    await expect(editor.locator("s")).toHaveCount(0);
    await expect(editor).toContainText(marker);
  });

  test("the link editor adds a link and refuses a non-http scheme", async () => {
    const marker = uniq("Linked");
    await typeFresh(page, marker);
    await page.keyboard.press("ControlOrMeta+a");
    await page.getByRole("button", { name: "Add or edit link" }).click();

    const urlField = page.getByTestId("draft-description-link-url");
    await expect(urlField).toBeVisible();

    // A dangerous scheme cannot even be submitted: Apply stays disabled.
    await urlField.fill("javascript:alert(1)");
    await expect(page.getByTestId("draft-description-link-apply")).toBeDisabled();

    await urlField.fill("https://example.com/product");
    await page.getByTestId("draft-description-link-apply").click();

    const link = page.getByTestId("draft-description-editor").locator("a");
    await expect(link).toHaveAttribute("href", "https://example.com/product");
    await expect(link).toContainText(marker);
  });

  test("pasted hostile markup cannot introduce a script or an unsafe link", async () => {
    await pasteHtml(
      page,
      '<p>Pasted <b>copy</b></p><script>window.__pwned = true;</script>' +
        '<iframe src="https://evil.example/x"></iframe>' +
        '<a href="javascript:alert(1)">bad link</a>' +
        '<p onclick="steal()">handler</p>',
    );

    const editor = page.getByTestId("draft-description-editor");
    await expect(editor).toContainText("Pasted");

    // Nothing executed, and nothing dangerous reached the document.
    expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__pwned)).toBe(
      undefined,
    );
    await expect(editor.locator("script")).toHaveCount(0);
    await expect(editor.locator("iframe")).toHaveCount(0);
    const unsafeHrefs = await editor.locator("a").evaluateAll((nodes) =>
      nodes.map((node) => (node as HTMLAnchorElement).getAttribute("href") ?? ""),
    );
    expect(unsafeHrefs.some((href) => href.toLowerCase().startsWith("javascript:"))).toBe(false);

    await saveAndSettle(page);
    await page.reload();
    const reloaded = page.getByTestId("draft-description-editor");
    await expect(reloaded).toBeVisible({ timeout: 30_000 });
    await expect(reloaded.locator("script")).toHaveCount(0);
    await expect(reloaded.locator("iframe")).toHaveCount(0);
    await expect(reloaded).toContainText("Pasted");
  });

  test("pasted rich text keeps allowed formatting and drops the rest", async () => {
    // The shape a Word/Google Docs paste actually arrives in: real
    // formatting wrapped in font tags, classes and inline styles.
    await pasteHtml(
      page,
      '<p class="MsoNormal" style="font-family:Calibri"><b>Bolded</b> and ' +
        '<span style="color:#ff0000"><i>italic</i></span></p>' +
        '<ul><li style="margin:0"><span lang="EN-GB">Bullet</span></li></ul>',
    );

    const editor = page.getByTestId("draft-description-editor");
    await expect(editor.locator("strong")).toContainText("Bolded");
    await expect(editor.locator("em")).toContainText("italic");
    await expect(editor.locator("ul li")).toContainText("Bullet");
    // Presentational carry-over is gone rather than merely invisible.
    await expect(editor.locator("[style]")).toHaveCount(0);
    await expect(editor.locator("font")).toHaveCount(0);
  });

  test("an over-limit description is flagged before the merchant tries to save", async () => {
    const editor = page.getByTestId("draft-description-editor");
    await editor.click();
    await page.keyboard.press("ControlOrMeta+a");
    await page.keyboard.press("Delete");
    // `insertText` delivers the whole string as one input event; typing it
    // character by character would take minutes.
    await page.keyboard.insertText("x".repeat(64_050));

    await expect(page.getByTestId("draft-description-too-long")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("draft-description-length")).toContainText("64,0");
  });

  test("a failed save leaves the merchant's text in the editor", async () => {
    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      if (route.request().method() !== "PATCH") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ code: "internal_error", message: "Boom.", details: [] }),
      });
    });

    const marker = uniq("Survives a failure");
    await typeFresh(page, marker);
    await visibleTestId(page, "save-draft").click({ timeout: 3000 }).catch(() => {});

    await expect(page.getByTestId("draft-description-editor")).toContainText(marker);
    await expect(visibleTestId(page, "save-draft")).toBeEnabled({ timeout: 10_000 });
  });

  test("a conflict freezes the editor without discarding the description", async () => {
    // M2A's protections must still hold for the new surface: on a 409 the
    // editor goes read-only pending the merchant's choice, and the text
    // they were writing stays on screen.
    await page.route("**/api/v1/drafts/*", async (route: Route) => {
      if (route.request().method() !== "PATCH") {
        await route.continue();
        return;
      }
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

    const marker = uniq("Unsaved during conflict");
    await typeFresh(page, marker);
    await visibleTestId(page, "save-draft").click({ timeout: 3000 }).catch(() => {});

    await expect(page.getByTestId("draft-conflict-banner")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("conflict-reload-latest")).toBeVisible();
    await expect(page.getByTestId("conflict-review-mine")).toBeVisible();

    const editor = page.getByTestId("draft-description-editor");
    await expect(editor).toContainText(marker);
    await expect(editor).toHaveAttribute("contenteditable", "false");
  });

  test("the toolbar is fully reachable and operable from the keyboard", async () => {
    const marker = uniq("Keyboard");
    await typeFresh(page, marker);
    await page.keyboard.press("ControlOrMeta+a");

    const bold = page.getByRole("button", { name: "Bold" });
    await bold.focus();
    await expect(bold).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("draft-description-editor").locator("strong")).toContainText(
      marker,
    );
    await expect(bold).toHaveAttribute("aria-pressed", "true");
  });

  test("the editor renders without console errors and without horizontal overflow", async () => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));

    await page.reload();
    await expect(page.getByTestId("draft-description-editor")).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Bulleted list" }).click();
    await page.waitForTimeout(500);

    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(overflows, "the page must not scroll horizontally at this viewport").toBe(false);

    // The toolbar wraps rather than clipping, so every control stays on
    // screen at the running project's viewport (mobile-chrome included).
    await expect(page.getByRole("button", { name: "Clear formatting" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Redo" })).toBeVisible();

    expect(errors, `console errors: ${errors.join(" | ")}`).toEqual([]);
  });

  test("the editor is legible in dark mode", async () => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.reload();
    const editor = page.getByTestId("draft-description-editor");
    await expect(editor).toBeVisible({ timeout: 30_000 });

    const { text, background } = await editor.evaluate((node) => {
      const style = window.getComputedStyle(node);
      // The editor itself is transparent by design; the bordered shell it
      // sits in owns the background.
      const shell = node.closest("div.rounded-md") ?? node;
      return {
        text: style.color,
        background: window.getComputedStyle(shell).backgroundColor,
      };
    });

    expect(text).not.toBe(background);
    await expect(page.getByRole("toolbar", { name: "Text formatting" })).toBeVisible();
    await page.emulateMedia({ colorScheme: null });
  });
});
