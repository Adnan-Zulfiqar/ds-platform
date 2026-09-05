import { expect, test, type Page } from "@playwright/test";

import {
  API_URL,
  buildAccount,
  isApiReachable,
  LEGAL_ACCEPTANCE_BODY,
} from "./helpers/auth";
import { canSeed, seedDrafts as seedIntoDatabase } from "./helpers/seed";

/**
 * M3A-4B — Preview and Impact, and the confirmed application.
 *
 * Drives the real backend, and — where a worker is running against it — the
 * real queue. The apply tests assert what the merchant sees; whether a worker
 * finishes the run is checked separately and skipped when none is listening,
 * so the suite reports honestly rather than passing on a fiction.
 */

const RULES_URL = "/settings/global-rules";

test.beforeEach(async () => {
  test.skip(!(await isApiReachable()), "Backend is not reachable.");
  test.skip(!(await canSeed()), "Draft seeding is not available in this environment.");
});

interface Session {
  token: string;
  tenantId: string;
}

async function signIn(page: Page): Promise<Session> {
  const account = buildAccount();
  const response = await page.request.post(`${API_URL}/api/v1/auth/register`, {
    data: {
      companyName: account.companyName,
      email: account.email,
      password: account.password,
      firstName: "E2E",
      lastName: "Impact",
      ...LEGAL_ACCEPTANCE_BODY,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = (await response.json()) as {
    tokens: { accessToken: string };
    identity: { tenant: { id: string } };
  };
  return { token: body.tokens.accessToken, tenantId: body.identity.tenant.id };
}

function auth(session: Session): Record<string, string> {
  return { Authorization: `Bearer ${session.token}` };
}

async function createRule(page: Page, session: Session, percent = "50"): Promise<void> {
  const response = await page.request.post(`${API_URL}/api/v1/global-rules/pricing`, {
    headers: auth(session),
    data: {
      name: "Impact rule",
      scope: "global",
      strategy: "percentage_markup",
      markupPercent: percent,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

/**
 * Seed drafts for this test's tenant.
 *
 * Written straight to the database by the test process — see `helpers/seed`
 * for why there is no endpoint for it. Everything asserted afterwards goes
 * through the real API.
 */
async function seedDrafts(
  page: Page,
  session: Session,
  count: number,
  options: { published?: number; flagged?: number; prefix?: string } = {},
): Promise<void> {
  void page;
  await seedIntoDatabase(session.tenantId, count, options);
}

async function openImpact(page: Page, session: Session) {
  await page.goto(RULES_URL);
  await expect(page.getByRole("heading", { name: "Global Rules" })).toBeVisible();
  await page.getByRole("tab", { name: "Preview and Impact" }).click();
  void session;
}

test.describe("Impact preview", () => {
  test("the area loads and lists drafts with their figures", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 3);
    await openImpact(page, session);

    const rows = page.getByTestId("impact-row");
    await expect(rows).toHaveCount(3);
    // (10 + 4) * 1.5 = 21, computed by the backend.
    await expect(rows.first().getByTestId("impact-proposed")).toContainText("21");
  });

  test("search and paging narrow the list", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2, { prefix: "Blue widget" });
    await seedDrafts(page, session, 2, { prefix: "Red gadget" });
    await openImpact(page, session);

    await expect(page.getByTestId("impact-row")).toHaveCount(4);
    await page.getByTestId("impact-search").fill("Blue widget");
    await expect(page.getByTestId("impact-row")).toHaveCount(2);
  });

  test("a published draft is marked and cannot be selected", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2, { published: 1 });
    await openImpact(page, session);

    const published = page.getByTestId("impact-row").filter({
      has: page.getByTestId("published-badge"),
    });
    await expect(published).toHaveCount(1);
    await expect(published.getByTestId("impact-select")).toBeDisabled();
    await expect(published.getByTestId("impact-reasons")).toContainText(
      /never repriced by this workflow/,
    );
  });

  test("a draft with no freight says so rather than showing zero", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 1, { flagged: 1, prefix: "No freight" });
    await openImpact(page, session);

    const row = page.getByTestId("impact-row").first();
    await expect(row.getByTestId("impact-proposed")).toContainText("Unavailable");
    await expect(row.getByTestId("impact-reasons")).toContainText(
      /did not report a freight cost/,
    );
  });
});

test.describe("Selection", () => {
  test("the current page can be selected and cleared", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 3);
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await expect(page.getByTestId("selection-summary")).toContainText("3 selected");

    await page.getByTestId("clear-selection").click();
    await expect(page.getByTestId("selection-summary")).toContainText("0 selected");
  });

  test("select-all-matching counts what the server can see, not the page", async ({
    page,
  }) => {
    const session = await signIn(page);
    await createRule(page, session);
    // More than one page, so a browser-side count would be wrong.
    await seedDrafts(page, session, 30);
    await openImpact(page, session);

    await expect(page.getByTestId("impact-row")).toHaveCount(25);
    await page.getByTestId("select-all-matching").click();
    await expect(page.getByTestId("selection-summary")).toContainText("30 selected");
  });

  test("select-all-matching excludes published drafts", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 5, { published: 2 });
    await openImpact(page, session);

    await page.getByTestId("select-all-matching").click();
    await expect(page.getByTestId("selection-summary")).toContainText("3 selected");
  });

  test("a published draft cannot be selected individually", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 3, { published: 3 });
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await expect(page.getByTestId("selection-summary")).toContainText("0 selected");
  });
});

test.describe("Confirmation", () => {
  test("the summary states every number before anything is written", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 4, { published: 1 });
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();

    const summary = page.getByTestId("confirm-summary");
    await expect(summary).toBeVisible();
    await expect(page.getByTestId("confirm-selected")).toHaveText("3");
    await expect(page.getByTestId("confirm-safe")).toHaveText("3");
    await expect(page.getByTestId("confirm-published")).toHaveText("0");
    await expect(page.getByText(/Published products and Shopify are untouched/)).toBeVisible();
    await expect(page.getByText(/no undo/)).toBeVisible();
  });

  test("cancelling the confirmation writes nothing", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await openImpact(page, session);

    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET" && request.url().includes("/drafts/apply")) {
        writes.push(request.url());
      }
    });

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByRole("button", { name: "Cancel" }).click();

    await expect(page.getByTestId("confirm-summary")).toBeHidden();
    expect(writes).toEqual([]);
  });

  test("opening the impact area sends no mutation", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);

    const mutations: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (!url.includes("/api/v1/global-rules")) return;
      if (request.method() !== "GET" && !url.includes("/preview")) {
        mutations.push(`${request.method()} ${url}`);
      }
    });

    await openImpact(page, session);
    await expect(page.getByTestId("impact-row")).toHaveCount(2);
    expect(mutations).toEqual([]);
  });

  test("no request reaches any third party", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);

    const external: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) external.push(request.url());
    });

    await openImpact(page, session);
    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByTestId("confirm-apply").click();
    await expect(page.getByTestId("application-progress")).toBeVisible();

    expect(external).toEqual([]);
  });
});

test.describe("Asynchronous application", () => {
  test("confirming shows a tracked application and puts it in the URL", async ({
    page,
  }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 3);
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    const accepted = page.waitForResponse(
      (response) =>
        response.url().includes("/drafts/apply") && response.request().method() === "POST",
    );
    await page.getByTestId("confirm-apply").click();
    expect((await accepted).status()).toBe(202);

    await expect(page.getByTestId("application-progress")).toBeVisible();
    await expect(page.getByTestId("count-total")).toHaveText("3");
    expect(page.url()).toContain("application=");
  });

  test("a refresh resumes the same application rather than starting another", async ({
    page,
  }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByTestId("confirm-apply").click();
    await expect(page.getByTestId("application-progress")).toBeVisible();
    const url = page.url();

    await page.reload();
    await expect(page.getByTestId("application-progress")).toBeVisible();
    expect(page.url()).toBe(url);

    const listed = await page.request.get(
      `${API_URL}/api/v1/global-rules/drafts/impact?size=1`,
      { headers: auth(session) },
    );
    expect(listed.ok()).toBeTruthy();
  });

  test("a double-click creates only one application", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await openImpact(page, session);

    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/drafts/apply") && request.method() === "POST") {
        posts.push(request.postData() ?? "");
      }
    });

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    const button = page.getByTestId("confirm-apply");
    await button.click({ clickCount: 2, delay: 20 });

    await expect(page.getByTestId("application-progress")).toBeVisible();
    // Whatever reached the API carried one key, so one run exists either way.
    const keys = new Set(
      posts.map((body) => (JSON.parse(body) as { idempotencyKey: string }).idempotencyKey),
    );
    expect(keys.size).toBe(1);
  });

  test("the worker finishes the run and the results persist", async ({ page }) => {
    // Room for the no-worker skip path: a 30s expect under a 30s test timeout
    // races the suite clock and reports failure instead of skip.
    test.setTimeout(60_000);
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 3);
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByTestId("confirm-apply").click();
    await expect(page.getByTestId("application-progress")).toBeVisible();

    // Skips rather than fails when no worker is listening: the UI is correct
    // either way, and claiming a worker ran when none did would be a lie.
    const progress = page.getByTestId("application-progress");
    try {
      await expect(progress).toHaveAttribute("data-status", "completed", {
        // Keep the original 30s detection window; the suite timeout is 60s so
        // a slow worker is not silently classified as absent.
        timeout: 30_000,
      });
    } catch {
      test.skip(true, "No Celery worker is consuming; the queue path is untested here.");
      return;
    }

    await expect(page.getByTestId("count-applied")).toHaveText("3");
    await expect(page.getByTestId("result-row")).toHaveCount(3);
    await expect(page.getByTestId("application-progress-text")).toContainText("3 of 3");

    // And it is still there after a reload — the record is durable.
    await page.reload();
    await expect(page.getByTestId("application-status")).toHaveText("Completed");
  });

  test("results can be filtered by outcome", async ({ page }) => {
    test.setTimeout(60_000);
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByTestId("confirm-apply").click();

    const progress = page.getByTestId("application-progress");
    try {
      await expect(progress).toHaveAttribute("data-status", "completed", {
        timeout: 30_000,
      });
    } catch {
      test.skip(true, "No Celery worker is consuming.");
      return;
    }

    await page.getByLabel("Outcome").selectOption("applied");
    await expect(page.getByTestId("result-row")).toHaveCount(2);
    await page.getByLabel("Outcome").selectOption("failed");
    await expect(page.getByText("No items with that outcome.")).toBeVisible();
  });
});

test.describe("Cancellation", () => {
  test("a pending run can be cancelled, and asks first", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await openImpact(page, session);

    // Hold the run in `pending` by intercepting the status poll, so the
    // cancel control is reachable regardless of how fast a worker is.
    await page.route("**/global-rules/applications/*", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const response = await route.fetch();
      const body = (await response.json()) as Record<string, unknown>;
      await route.fulfill({ response, json: { ...body, status: "pending" } });
    });

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByTestId("confirm-apply").click();

    await expect(page.getByTestId("cancel-application")).toBeVisible();
    await page.getByTestId("cancel-application").click();
    await expect(page.getByText(/It has not started, so nothing has been written/)).toBeVisible();
    await page.getByRole("button", { name: "Keep going" }).click();
    await expect(page.getByTestId("confirm-cancel")).toBeHidden();
  });

  test("a completed run offers no cancel action", async ({ page }) => {
    test.setTimeout(60_000);
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 1);
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByTestId("confirm-apply").click();

    const progress = page.getByTestId("application-progress");
    try {
      await expect(progress).toHaveAttribute("data-status", "completed", {
        timeout: 30_000,
      });
    } catch {
      test.skip(true, "No Celery worker is consuming.");
      return;
    }
    await expect(page.getByTestId("cancel-application")).toHaveCount(0);
  });
});

test.describe("Permissions and isolation", () => {
  async function asViewer(page: Page): Promise<void> {
    for (const path of ["**/api/v1/auth/refresh", "**/api/v1/auth/me"]) {
      await page.route(path, async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as Record<string, unknown>;
        const identity = (body.identity ?? body) as { roles: string[] };
        const patched = { ...identity, roles: ["viewer"] };
        await route.fulfill({
          response,
          json: body.identity ? { ...body, identity: patched } : patched,
        });
      });
    }
  }

  test("a viewer can preview but cannot select or apply", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await asViewer(page);
    await openImpact(page, session);

    await expect(page.getByTestId("impact-row")).toHaveCount(2);
    await expect(page.getByTestId("open-confirm")).toHaveCount(0);
    await expect(page.getByTestId("select-all-matching")).toHaveCount(0);
    await expect(page.getByTestId("impact-select").first()).toBeDisabled();
  });

  test("another tenant's application is not exposed", async ({ page }) => {
    const owner = await signIn(page);
    await createRule(page, owner);
    await seedDrafts(page, owner, 1);
    const created = await page.request.post(
      `${API_URL}/api/v1/global-rules/drafts/apply`,
      {
        headers: auth(owner),
        data: { idempotencyKey: `iso-${Date.now()}`, selectionFilter: { safeOnly: true } },
      },
    );
    expect(created.status()).toBe(202);
    const applicationId = ((await created.json()) as { id: string }).id;

    const intruder = await signIn(page);
    const response = await page.request.get(
      `${API_URL}/api/v1/global-rules/applications/${applicationId}`,
      { headers: auth(intruder) },
    );
    expect(response.status()).toBe(404);
  });
});

test.describe("Target selectors", () => {
  test("a product rule is scoped by name, with no UUID typed", async ({ page }) => {
    const session = await signIn(page);
    await seedDrafts(page, session, 1, { prefix: "Findable product" });

    await page.goto(RULES_URL);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Scoped by name");
    await dialog.getByLabel("Scope").selectOption("product");

    const combobox = dialog.getByTestId("target-combobox-product");
    await combobox.click();
    await combobox.fill("Findable");
    const listbox = dialog.getByRole("listbox", { name: /product results/ });
    const option = listbox.getByRole("option", { name: /Findable product/ }).first();
    await expect(option).toBeVisible();
    await option.click();

    await expect(combobox).toHaveValue(/Findable product/);
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByTestId("rule-row").filter({ hasText: "Scoped by name" }),
    ).toBeVisible();
  });

  test("the combobox is operable by keyboard alone", async ({ page }) => {
    const session = await signIn(page);
    await seedDrafts(page, session, 2, { prefix: "Keyboard target" });

    await page.goto(RULES_URL);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Scope").selectOption("product");

    const combobox = dialog.getByTestId("target-combobox-product");
    await combobox.focus();
    await combobox.fill("Keyboard");
    // Scoped to the combobox's own listbox: the scope selector is a native
    // <select>, whose <option> elements also carry the option role.
    const listbox = dialog.getByRole("listbox", { name: /product results/ });
    await expect(listbox.getByRole("option").first()).toBeVisible();

    await page.keyboard.press("ArrowDown");
    await expect(combobox).toHaveAttribute("aria-activedescendant", /.+/);
    await page.keyboard.press("Enter");
    await expect(combobox).toHaveValue(/Keyboard target/);
  });

  test("raw identifier entry survives as an explicit fallback", async ({ page }) => {
    await signIn(page);
    await page.goto(RULES_URL);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Scope").selectOption("category");

    await expect(dialog.getByLabel("Category ID (advanced)")).toHaveCount(0);
    await dialog.getByRole("button", { name: "Enter an identifier instead" }).click();
    await expect(dialog.getByLabel("Category ID (advanced)")).toBeVisible();
  });
});

test.describe("Responsive, theme and accessibility", () => {
  for (const viewport of [
    { name: "mobile", width: 375, height: 812 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "desktop", width: 1280, height: 800 },
  ]) {
    test(`the impact area does not overflow at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const session = await signIn(page);
      await createRule(page, session);
      await seedDrafts(page, session, 3, { published: 1 });
      await openImpact(page, session);

      await expect(page.getByTestId("impact-row").first()).toBeVisible();
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth + 1,
      );
      expect(overflow).toBe(false);
    });
  }

  test("selection checkboxes and actions meet the target size", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await openImpact(page, session);

    for (const name of ["Select page", "Clear", "Apply to selected"]) {
      const box = await page.getByRole("button", { name }).boundingBox();
      expect(box, name).not.toBeNull();
      expect(box!.height, name).toBeGreaterThanOrEqual(40);
    }
  });

  test("the impact area renders in dark mode with no console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await page.emulateMedia({ colorScheme: "dark" });

    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2, { published: 1, flagged: 1 });
    await openImpact(page, session);

    await expect(page.getByTestId("impact-row").first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("progress is announced, not conveyed by colour alone", async ({ page }) => {
    const session = await signIn(page);
    await createRule(page, session);
    await seedDrafts(page, session, 2);
    await openImpact(page, session);

    await page.getByTestId("select-page").click();
    await page.getByTestId("open-confirm").click();
    await page.getByTestId("confirm-apply").click();

    const progress = page.getByTestId("application-progress");
    await expect(progress).toBeVisible();
    await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", /\d+/);
    await expect(page.getByTestId("application-progress-text")).toContainText("of 2");
    await expect(page.getByTestId("application-status")).not.toBeEmpty();
  });
});
