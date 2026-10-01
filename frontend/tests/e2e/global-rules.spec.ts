import { expect, test, type Page } from "@playwright/test";

import {
  API_URL,
  buildAccount,
  isApiReachable,
  LEGAL_ACCEPTANCE_BODY,
} from "./helpers/auth";

/**
 * M3A-4A — Settings → Global Rules.
 *
 * Drives the real backend: rules are created, edited, activated and versioned
 * through the actual API, and the assertions are about what the merchant sees
 * afterwards. Validation is deliberately **not** stubbed — a test that mocked
 * the API away would keep passing after the contract changed underneath it,
 * which is the one thing these tests exist to catch.
 *
 * The single exception is the viewer-role test, which shapes the identity
 * response. There is no endpoint that creates a second user with a viewer role
 * in the same tenant, so a real viewer session cannot be produced from the UI.
 * That test therefore covers the *UI* boundary only; the security boundary is
 * covered by the backend suite, which rejects a viewer's write regardless of
 * what the interface renders.
 */

const RULES_URL = "/settings/global-rules";

test.beforeEach(async () => {
  test.skip(!(await isApiReachable()), "Backend is not reachable.");
});

/**
 * Register a fresh workspace and land signed in.
 *
 * Deliberately *not* `registerViaApiAndSignIn`: `page.request` shares the
 * browser context's cookie jar, so registering through the API already leaves
 * the session cookie in the browser. Going to `/login` afterwards renders the
 * form for a moment and then redirects as the provider restores the session —
 * which detaches the email input mid-fill and fails as a timeout that looks
 * like a selector problem. Navigating straight to the destination exercises
 * the same restore path the app uses on every reload.
 */
async function signIn(page: Page): Promise<void> {
  const account = buildAccount();
  const response = await page.request.post(`${API_URL}/api/v1/auth/register`, {
    data: {
      companyName: account.companyName,
      email: account.email,
      password: account.password,
      firstName: "E2E",
      lastName: "Operator",
      ...LEGAL_ACCEPTANCE_BODY,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function openRules(page: Page) {
  await signIn(page);
  await page.goto(RULES_URL);
  await expect(page.getByRole("heading", { name: "Global Rules" })).toBeVisible();
}

/**
 * Capture the Authorization header the app itself sends.
 *
 * The access token lives only in memory, so there is nowhere to read it from.
 * Sniffing a real request avoids calling `/auth/refresh` from the test, which
 * rotates the refresh token and would put the page's own session at risk of
 * reuse detection.
 */
function watchAuthHeader(page: Page): () => Record<string, string> {
  let header: string | null = null;
  page.on("request", (request) => {
    if (!request.url().includes("/api/v1/")) return;
    const value = request.headers()["authorization"];
    if (value) header = value;
  });
  return () => {
    if (!header) throw new Error("No authenticated request was observed yet.");
    return { Authorization: header };
  };
}

/**
 * Report the session as a viewer.
 *
 * Both the restore path (`/auth/refresh`) and the explicit read (`/auth/me`)
 * carry the roles, and the provider seeds identity from whichever answers
 * first — so intercepting only one leaves the other to put the admin roles
 * straight back.
 */
async function asViewer(page: Page): Promise<void> {
  await page.route("**/api/v1/auth/refresh", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { identity: { roles: string[] } };
    await route.fulfill({
      response,
      json: { ...body, identity: { ...body.identity, roles: ["viewer"] } },
    });
  });
  await page.route("**/api/v1/auth/me", async (route) => {
    const response = await route.fetch();
    const identity = (await response.json()) as { roles: string[] };
    await route.fulfill({ response, json: { ...identity, roles: ["viewer"] } });
  });
}

async function section(page: Page, name: string) {
  await page.getByRole("tab", { name }).click();
}

/** Create a pricing rule through the form and wait for the list to show it. */
async function createPricingRule(
  page: Page,
  options: { name: string; strategy?: string; percent?: string },
) {
  await page.getByTestId("new-pricing-rule").click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Rule name").fill(options.name);
  if (options.strategy) {
    await dialog.getByLabel("Pricing strategy").selectOption(options.strategy);
  }
  if (options.percent) {
    const field =
      options.strategy === "target_margin"
        ? "Gross margin percentage"
        : "Markup percentage";
    await dialog.getByLabel(field).fill(options.percent);
  }
  await dialog.getByRole("button", { name: "Create rule" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("rule-row").filter({ hasText: options.name })).toBeVisible();
}

test.describe("Navigation and access", () => {
  test("the settings index links to global rules", async ({ page }) => {
    await signIn(page);
    await page.goto("/settings");
    await page.getByRole("link", { name: /Global Rules/ }).click();
    await expect(page).toHaveURL(new RegExp(RULES_URL));
    await expect(page.getByRole("heading", { name: "Global Rules" })).toBeVisible();
  });

  test("the route loads directly and survives a refresh", async ({ page }) => {
    await openRules(page);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Global Rules" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Pricing Rules" })).toBeVisible();
  });

  test("signed-out visitors are sent to sign in", async ({ page }) => {
    await page.context().clearCookies();
    await page.goto(RULES_URL);
    await page.waitForURL(/\/login/);
    expect(page.url()).toContain("next=");
  });

  test("an empty workspace explains itself rather than looking broken", async ({
    page,
  }) => {
    await openRules(page);
    await expect(page.getByText("No pricing rules yet")).toBeVisible();
    await expect(
      page.getByText(/keep their supplier price until a rule exists/),
    ).toBeVisible();
  });

  test("the page loads with no console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await openRules(page);
    await section(page, "Shipping Rules");
    await section(page, "Application Behaviour");
    await section(page, "Rule History");
    expect(errors).toEqual([]);
  });
});

test.describe("Pricing rules", () => {
  test("a markup rule can be created and appears in the list", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Standard markup", percent: "50" });
    const row = page.getByTestId("rule-row").filter({ hasText: "Standard markup" });
    await expect(row).toContainText("50% markup on landed cost");
    await expect(row).toContainText("Active");
    await expect(row.getByTestId("rule-version")).toHaveText("1");
  });

  test("the dialog closes only once the saved rule is in the list", async ({ page }) => {
    // Regression for N-5: the dialog used to close as soon as the POST
    // returned, while the list refetch was still in flight, so the new rule
    // was briefly missing. Slow the list refetch on purpose; the row must
    // already be there the moment the dialog is gone.
    await openRules(page);
    let slowListResponses = 0;
    await page.route(
      (url) => /\/api\/v1\/global-rules\/pricing$/.test(url.pathname),
      async (route) => {
        if (route.request().method() === "GET") {
          slowListResponses += 1;
          await new Promise((resolve) => setTimeout(resolve, 3_000));
        }
        return route.fallback();
      },
    );
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Slow list rule");
    await dialog.getByLabel("Markup percentage").fill("40");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
    await expect(
      page.getByTestId("rule-row").filter({ hasText: "Slow list rule" }),
    ).toBeVisible({ timeout: 500 });
    expect(slowListResponses).toBeGreaterThan(0);
  });

  test("each strategy shows only the fields it uses", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");

    await dialog.getByLabel("Pricing strategy").selectOption("percentage_markup");
    await expect(dialog.getByLabel("Markup percentage")).toBeVisible();
    await expect(dialog.getByLabel("Gross margin percentage")).toBeHidden();

    await dialog.getByLabel("Pricing strategy").selectOption("target_margin");
    await expect(dialog.getByLabel("Gross margin percentage")).toBeVisible();
    await expect(dialog.getByLabel("Markup percentage")).toBeHidden();

    await dialog.getByLabel("Pricing strategy").selectOption("fixed_markup");
    await expect(dialog.getByLabel("Fixed amount")).toBeVisible();
    await expect(dialog.getByLabel("Markup percentage")).toBeHidden();

    await dialog.getByLabel("Pricing strategy").selectOption("hybrid");
    await expect(dialog.getByLabel("Markup percentage")).toBeVisible();
    await expect(dialog.getByLabel("Fixed amount")).toBeVisible();
  });

  test("every strategy can be saved", async ({ page }) => {
    await openRules(page);

    // Scoped to distinct categories rather than global: the schema permits
    // only one *active global* pricing rule per workspace, so four global
    // rules would collide on that constraint rather than on anything this
    // test is about.
    const cases = [
      { name: "Fixed profit rule", strategy: "fixed_markup", field: "Fixed amount", value: "6" },
      { name: "Markup rule", strategy: "percentage_markup", field: "Markup percentage", value: "45" },
      { name: "Margin rule", strategy: "target_margin", field: "Gross margin percentage", value: "35" },
      { name: "Hybrid rule", strategy: "hybrid", field: "Fixed amount", value: "2" },
    ];

    for (const [index, entry] of cases.entries()) {
      await page.getByTestId("new-pricing-rule").click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Rule name").fill(entry.name);
      await dialog.getByLabel("Scope").selectOption("category");
      // Categories are picked by name now; this reaches the identifier box
      // deliberately, because the subject here is the strategy fields.
      await dialog.getByRole("button", { name: "Enter an identifier instead" }).click();
      await dialog.getByLabel("Category ID (advanced)").fill(`cat-${index}`);
      await dialog.getByLabel("Pricing strategy").selectOption(entry.strategy);
      await dialog.getByLabel(entry.field).fill(entry.value);
      await dialog.getByRole("button", { name: "Create rule" }).click();
      await expect(dialog).toBeHidden();
      await expect(
        page.getByTestId("rule-row").filter({ hasText: entry.name }),
      ).toBeVisible();
    }

    await expect(page.getByTestId("rule-row")).toHaveCount(cases.length);
  });

  test("markup and margin are explained with a worked example", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const example = page.getByTestId("markup-margin-example");
    await expect(example).toBeVisible();
    await expect(example).toContainText("50% markup = £15");
    await expect(example).toContainText("50% gross margin = £20");
  });

  test("a target margin of 100% or more is refused", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Impossible margin");
    await dialog.getByLabel("Pricing strategy").selectOption("target_margin");
    await dialog.getByLabel("Gross margin percentage").fill("100");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog.getByText("Gross margin must be below 100%.")).toBeVisible();
    await expect(dialog).toBeVisible();
  });

  test("a rule without a name is refused", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Markup percentage").fill("25");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog.getByText("Give the rule a name.")).toBeVisible();
  });

  test("a global rule cannot target a scope identifier", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByLabel("Scope")).toHaveValue("global");
    await expect(dialog.getByTestId("target-combobox-product")).toHaveCount(0);
    await expect(dialog.getByTestId("target-combobox-category")).toHaveCount(0);

    await dialog.getByLabel("Scope").selectOption("category");
    await expect(dialog.getByTestId("target-combobox-category")).toBeVisible();

    await dialog.getByLabel("Scope").selectOption("product");
    await expect(dialog.getByTestId("target-combobox-product")).toBeVisible();
    await expect(dialog.getByTestId("target-combobox-category")).toHaveCount(0);
  });

  test("a scoped rule needs a target chosen", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Category rule");
    await dialog.getByLabel("Scope").selectOption("category");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog.getByText(/needs its Category ID/)).toBeVisible();
  });

  // The advanced fallback still validates: pasting something that is not an
  // identifier is refused rather than saved and silently matching nothing.
  test("a product-scoped rule rejects a non-identifier", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Product rule");
    await dialog.getByLabel("Scope").selectOption("product");
    await dialog.getByRole("button", { name: "Enter an identifier instead" }).click();
    await dialog.getByLabel("Product ID (advanced)").fill("not-a-uuid");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog.getByText(/must be a valid identifier/)).toBeVisible();
  });

  test("currency and rounding are saved and read back", async ({ page }) => {
    await openRules(page);
    await page.getByTestId("new-pricing-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Rounded GBP rule");
    await dialog.getByLabel("Markup percentage").fill("60");
    await dialog.getByLabel("Rounding").selectOption("ninety_nine");
    await dialog.getByLabel("Currency").fill("GBP");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog).toBeHidden();

    await page
      .getByTestId("rule-row")
      .filter({ hasText: "Rounded GBP rule" })
      .getByRole("button", { name: "Edit" })
      .click();
    const editor = page.getByRole("dialog");
    await expect(editor.getByLabel("Rounding")).toHaveValue("ninety_nine");
    await expect(editor.getByLabel("Currency")).toHaveValue("GBP");
  });

  test("a rule can be edited and its version increments", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Editable rule", percent: "40" });

    const row = page.getByTestId("rule-row").filter({ hasText: "Editable rule" });
    await row.getByRole("button", { name: "Edit" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Markup percentage").fill("75");
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect(dialog).toBeHidden();

    await expect(row).toContainText("75% markup on landed cost");
    await expect(row.getByTestId("rule-version")).toHaveText("2");
  });

  test("a rule can be deactivated and reactivated", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Toggle rule", percent: "30" });
    const row = page.getByTestId("rule-row").filter({ hasText: "Toggle rule" });

    await row.getByRole("button", { name: "Deactivate" }).click();
    await expect(row).toContainText("Inactive");

    await row.getByRole("button", { name: "Activate" }).click();
    await expect(row).toContainText("Active");
  });

  test("the applies-to-new-imports toggle round-trips", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Import rule", percent: "20" });

    await section(page, "Application Behaviour");
    const entry = page.getByTestId("behaviour-rule").filter({ hasText: "Import rule" });
    const toggle = entry.getByRole("switch", { name: "Apply to new imports" });
    await expect(toggle).toBeChecked();
    // `uncheck()` verifies the DOM flipped immediately, which a controlled
    // input driven by a server round-trip never does — it reverts until the
    // mutation lands. Click, then wait for the state the server produced.
    await toggle.click();
    await expect(toggle).not.toBeChecked();

    await page.reload();
    await section(page, "Application Behaviour");
    await expect(
      page
        .getByTestId("behaviour-rule")
        .filter({ hasText: "Import rule" })
        .getByRole("switch", { name: "Apply to new imports" }),
    ).not.toBeChecked();
  });

  test("saving a rule sends no request that would reprice products", async ({
    page,
  }) => {
    await openRules(page);
    const repriceCalls: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (
        request.method() !== "GET" &&
        (url.includes("/drafts/apply") ||
          url.includes("/pricing/apply") ||
          url.includes("/products"))
      ) {
        repriceCalls.push(`${request.method()} ${url}`);
      }
    });

    await createPricingRule(page, { name: "Safe save", percent: "45" });
    expect(repriceCalls).toEqual([]);
    await expect(
      page.getByText(/Saving a rule never changes an existing product/),
    ).toBeVisible();
  });
});

test.describe("Shipping rules", () => {
  test("the missing-freight limitation is stated up front", async ({ page }) => {
    await openRules(page);
    await section(page, "Shipping Rules");
    await expect(page.getByTestId("missing-freight-warning").first()).toContainText(
      "AliExpress imports currently provide no freight quote",
    );
    await expect(page.getByTestId("missing-freight-warning").first()).toContainText(
      "marked Needs review",
    );
  });

  test("a shipping rule can be created and edited", async ({ page }) => {
    await openRules(page);
    await section(page, "Shipping Rules");
    await page.getByTestId("new-shipping-rule").click();

    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("UK tracked");
    await dialog.getByLabel("Destination country").fill("GB");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog).toBeHidden();

    const row = page.getByTestId("rule-row").filter({ hasText: "UK tracked" });
    await expect(row).toContainText("Cheapest tracked to GB");

    await row.getByRole("button", { name: "Edit" }).click();
    const editor = page.getByRole("dialog");
    await editor.getByLabel("Maximum delivery days").fill("10");
    await editor.getByRole("button", { name: "Save changes" }).click();
    await expect(editor).toBeHidden();
    await expect(row).toContainText("≤ 10 days");
  });

  test("a destination country is required", async ({ page }) => {
    await openRules(page);
    await section(page, "Shipping Rules");
    await page.getByTestId("new-shipping-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("No destination");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(
      dialog.getByText(/shipping cost depends entirely on destination/i),
    ).toBeVisible();
  });

  test("fastest-below-cost requires a ceiling", async ({ page }) => {
    await openRules(page);
    await section(page, "Shipping Rules");
    await page.getByTestId("new-shipping-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Fast under cost");
    await dialog.getByLabel("Destination country").fill("GB");
    await dialog.getByLabel("Selection strategy").selectOption("fastest_under_cost");
    await expect(dialog.getByLabel("Maximum shipping cost")).toBeVisible();
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog.getByText(/needs a cost ceiling/)).toBeVisible();
  });

  test("a carrier cannot be both preferred and blocked", async ({ page }) => {
    await openRules(page);
    await section(page, "Shipping Rules");
    await page.getByTestId("new-shipping-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Contradictory carriers");
    await dialog.getByLabel("Destination country").fill("GB");
    await dialog.getByLabel("Preferred carriers").fill("DHL, Royal Mail");
    await dialog.getByLabel("Blocked carriers").fill("DHL");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(
      dialog.getByText(/cannot be both preferred and blocked/),
    ).toBeVisible();
  });

  test("negative days are refused", async ({ page }) => {
    await openRules(page);
    await section(page, "Shipping Rules");
    await page.getByTestId("new-shipping-rule").click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Rule name").fill("Negative days");
    await dialog.getByLabel("Destination country").fill("GB");
    await dialog.getByLabel("Maximum delivery days").fill("-3");
    await dialog.getByRole("button", { name: "Create rule" }).click();
    await expect(dialog.getByText(/whole number of days/)).toBeVisible();
  });
});

test.describe("Live preview", () => {
  test("the preview asks the backend and shows what it returns", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Preview rule", percent: "50" });
    await section(page, "Live Preview");

    const request = page.waitForRequest(
      (candidate) =>
        candidate.url().includes("/global-rules/preview") &&
        candidate.method() === "POST",
    );
    await page.getByLabel("Item cost").fill("10");
    await page.getByLabel("Supplier shipping").fill("4");
    const sent = await request;
    const body = sent.postDataJSON() as Record<string, unknown>;
    expect(body.itemCost).toBe("10");
    expect(body.shippingCost).toBe("4");

    // (10 + 4) * 1.5 — computed by the backend, not by this test's UI.
    await expect(page.getByTestId("preview-landed-cost")).toContainText("14");
    await expect(page.getByTestId("preview-price")).toContainText("21");
    await expect(page.getByTestId("preview-rule-name")).toContainText("Preview rule");
  });

  test("markup and margin are shown as different figures", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Margin display", percent: "50" });
    await section(page, "Live Preview");
    await page.getByLabel("Item cost").fill("10");
    await page.getByLabel("Supplier shipping").fill("4");

    await expect(page.getByTestId("preview-markup")).toContainText("50");
    // 7 / 21 = 33.33% margin — always lower than the markup.
    await expect(page.getByTestId("preview-margin")).toContainText("33.33");
  });

  test("a slow earlier response cannot overwrite a newer one", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Race rule", percent: "50" });
    await section(page, "Live Preview");

    // Delay the first preview long enough that the second overtakes it. If the
    // panel took whichever landed last, it would show the stale figures.
    let seen = 0;
    await page.route("**/global-rules/preview", async (route) => {
      seen += 1;
      if (seen === 1) await new Promise((resolve) => setTimeout(resolve, 2500));
      await route.continue();
    });

    await page.getByLabel("Item cost").fill("10");
    await page.waitForTimeout(600);
    await page.getByLabel("Item cost").fill("100");

    // 100 + 4 = 104 landed; the stale response would say 14.
    await expect(page.getByTestId("preview-landed-cost")).toContainText("104", {
      timeout: 15_000,
    });
    await page.waitForTimeout(2500);
    await expect(page.getByTestId("preview-landed-cost")).toContainText("104");
  });

  test("missing shipping explains why there is no price", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Fail closed", percent: "50" });
    await section(page, "Live Preview");

    await page.getByLabel("Item cost").fill("10");
    await page.getByLabel("Supplier shipping").fill("");

    const reasons = page.getByTestId("preview-review-reasons");
    await expect(reasons).toBeVisible();
    await expect(reasons).toContainText(/did not report a freight cost/);
    await expect(page.getByTestId("preview-price")).toContainText("—");
  });

  test("the preview writes nothing", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Read only", percent: "50" });
    await section(page, "Live Preview");

    const writes: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      const method = request.method();
      if (method === "GET" || !url.includes("/api/v1/")) return;
      // The preview is a POST by necessity — it carries a body. Anything else
      // reaching the API while only the calculator is in use is a write.
      if (!url.includes("/global-rules/preview")) writes.push(`${method} ${url}`);
    });

    await page.getByLabel("Item cost").fill("12");
    await page.getByLabel("Supplier shipping").fill("3");
    await expect(page.getByTestId("preview-landed-cost")).toContainText("15");
    expect(writes).toEqual([]);
  });
});

test.describe("Rule history", () => {
  test("history records every version and cannot be edited", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Audited rule", percent: "40" });

    const row = page.getByTestId("rule-row").filter({ hasText: "Audited rule" });
    await row.getByRole("button", { name: "Edit" }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("Markup percentage").fill("65");
    await dialog.getByLabel("Reason for this change").fill("Raising margin for Q4");
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect(dialog).toBeHidden();

    await row.getByRole("button", { name: "History" }).click();
    dialog = page.getByRole("dialog");
    const entries = dialog.getByTestId("history-entry");
    await expect(entries).toHaveCount(2);
    await expect(entries.first()).toContainText("Version 2");
    await expect(entries.first()).toContainText("Raising margin for Q4");

    // No destructive controls anywhere in the trail.
    await expect(dialog.getByRole("button", { name: /^Delete/ })).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: /^Revert/ })).toHaveCount(0);
  });

  test("change details disclose the previous and new value", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Diffed rule", percent: "40" });

    const row = page.getByTestId("rule-row").filter({ hasText: "Diffed rule" });
    await row.getByRole("button", { name: "Edit" }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("Markup percentage").fill("55");
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await expect(dialog).toBeHidden();

    await row.getByRole("button", { name: "History" }).click();
    dialog = page.getByRole("dialog");
    // Both versions must be rendered first: clicking while the panel is still
    // swapping its skeleton for content loses the click to a detached node.
    await expect(dialog.getByTestId("history-entry")).toHaveCount(2);
    // Scoped to the newest entry's own disclosure. Creating a rule records
    // every field as changed, so version 1 has a disclosure too, and a
    // dialog-wide `.first()` is only incidentally the right one.
    const latest = dialog.getByTestId("history-entry").first();
    const details = latest.getByRole("button", { name: /change details/ });
    await expect(details).toHaveAttribute("aria-expanded", "false");
    await details.click();
    await expect(details).toHaveAttribute("aria-expanded", "true");
    await expect(latest.getByText("Markup percent")).toBeVisible();
    await expect(latest.getByRole("cell", { name: "40.0000" })).toBeVisible();
    await expect(latest.getByRole("cell", { name: "55.0000" })).toBeVisible();
  });

  test("history paginates once there are more versions than a page", async ({
    page,
  }) => {
    test.slow();
    await openRules(page);
    await createPricingRule(page, { name: "Busy rule", percent: "10" });
    const row = page.getByTestId("rule-row").filter({ hasText: "Busy rule" });

    // Eleven versions in total: one create plus ten edits, one past the page.
    // Wait for each PATCH to succeed before reopening Edit — otherwise the
    // dialog can reopen with a stale `expectedUpdatedAt` from the list cache
    // and stay open on a 409 conflict banner.
    for (let percent = 11; percent <= 20; percent += 1) {
      await row.getByRole("button", { name: "Edit" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Markup percentage").fill(String(percent));
      const patched = page.waitForResponse((r) => {
        if (r.request().method() !== "PATCH") return false;
        return r.url().includes("/api/v1/global-rules/pricing/");
      });
      await dialog.getByRole("button", { name: "Save changes" }).click();
      expect((await patched).status()).toBe(200);
      await expect(dialog).toBeHidden();
      await expect(row).toContainText(`${percent}%`);
    }

    await row.getByRole("button", { name: "History" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByTestId("history-entry")).toHaveCount(10);
    await expect(dialog.getByText(/Page 1 of 2/)).toBeVisible();
    await dialog.getByRole("button", { name: "Next" }).click();
    await expect(dialog.getByText(/Page 2 of 2/)).toBeVisible();
    await expect(dialog.getByTestId("history-entry")).toHaveCount(1);
  });

  test("the history section reads a rule chosen from the list", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Selectable rule", percent: "35" });
    await section(page, "Rule History");
    await expect(page.getByTestId("history-entry")).toHaveCount(1);
    await expect(page.getByTestId("history-entry").first()).toContainText("Version 1");
  });
});

test.describe("Concurrency and unsaved work", () => {
  test("a stale save is refused and offers a way out", async ({ page }) => {
    const auth = watchAuthHeader(page);
    await openRules(page);
    await createPricingRule(page, { name: "Contested rule", percent: "50" });

    const row = page.getByTestId("rule-row").filter({ hasText: "Contested rule" });
    await row.getByRole("button", { name: "Edit" }).click();
    const dialog = page.getByRole("dialog");

    // Someone else saves first, using the API directly — the token this open
    // form is holding is now stale.
    const ruleId = await row.getAttribute("data-rule-id");
    const listed = await page.request.get(
      `${API_URL}/api/v1/global-rules/pricing/${ruleId}`,
      { headers: auth() },
    );
    const current = (await listed.json()) as { name: string; updatedAt: string };
    const elsewhere = await page.request.patch(
      `${API_URL}/api/v1/global-rules/pricing/${ruleId}`,
      {
        headers: auth(),
        data: {
          name: current.name,
          scope: "global",
          strategy: "percentage_markup",
          markupPercent: "90",
          expectedUpdatedAt: current.updatedAt,
        },
      },
    );
    expect(elsewhere.ok(), await elsewhere.text()).toBeTruthy();

    await dialog.getByLabel("Markup percentage").fill("70");
    await dialog.getByRole("button", { name: "Save changes" }).click();

    const banner = page.getByTestId("conflict-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Nothing has been saved");
    await expect(banner.getByRole("button", { name: "Reload latest version" })).toBeVisible();
    await expect(banner.getByRole("button", { name: "Keep my changes" })).toBeVisible();
  });

  test("reloading after a conflict adopts the server's version", async ({ page }) => {
    const auth = watchAuthHeader(page);
    await openRules(page);
    await createPricingRule(page, { name: "Reloadable rule", percent: "50" });

    const row = page.getByTestId("rule-row").filter({ hasText: "Reloadable rule" });
    const ruleId = await row.getAttribute("data-rule-id");
    await row.getByRole("button", { name: "Edit" }).click();
    const dialog = page.getByRole("dialog");

    const listed = await page.request.get(
      `${API_URL}/api/v1/global-rules/pricing/${ruleId}`,
      { headers: auth() },
    );
    const current = (await listed.json()) as { name: string; updatedAt: string };
    await page.request.patch(`${API_URL}/api/v1/global-rules/pricing/${ruleId}`, {
      headers: auth(),
      data: {
        name: current.name,
        scope: "global",
        strategy: "percentage_markup",
        markupPercent: "88",
        expectedUpdatedAt: current.updatedAt,
      },
    });

    await dialog.getByLabel("Markup percentage").fill("70");
    await dialog.getByRole("button", { name: "Save changes" }).click();
    await page.getByTestId("conflict-banner").getByRole("button", { name: "Reload latest version" }).click();

    await expect(page.getByTestId("conflict-banner")).toBeHidden();
    await expect(dialog.getByLabel("Markup percentage")).toHaveValue("88.0000");
  });

  test("save is disabled until something changes", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Untouched rule", percent: "50" });
    await page
      .getByTestId("rule-row")
      .filter({ hasText: "Untouched rule" })
      .getByRole("button", { name: "Edit" })
      .click();

    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await dialog.getByLabel("Markup percentage").fill("51");
    await expect(dialog.getByRole("button", { name: "Save changes" })).toBeEnabled();
    await expect(dialog.getByRole("button", { name: "Discard changes" })).toBeVisible();
  });

  test("closing a dirty form asks before discarding", async ({ page }) => {
    await openRules(page);
    await createPricingRule(page, { name: "Dirty rule", percent: "50" });
    await page
      .getByTestId("rule-row")
      .filter({ hasText: "Dirty rule" })
      .getByRole("button", { name: "Edit" })
      .click();

    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Markup percentage").fill("66");

    let asked = false;
    page.on("dialog", (confirmation) => {
      asked = true;
      void confirmation.dismiss();
    });
    await page.keyboard.press("Escape");
    await expect.poll(() => asked).toBe(true);
    await expect(dialog).toBeVisible();
  });

  test("opening the page sends no mutation", async ({ page }) => {
    await signIn(page);
    const mutations: string[] = [];
    page.on("request", (request) => {
      const method = request.method();
      const url = request.url();
      if (!url.includes("/api/v1/global-rules")) return;
      if (method !== "GET" && !url.includes("/preview")) {
        mutations.push(`${method} ${url}`);
      }
    });

    await page.goto(RULES_URL);
    await expect(page.getByRole("heading", { name: "Global Rules" })).toBeVisible();
    await page.waitForTimeout(1000);
    expect(mutations).toEqual([]);
  });
});

test.describe("Permissions", () => {
  test("a viewer sees no mutation controls", async ({ page }) => {
    // The API has no endpoint that creates a second user with a viewer role in
    // the same tenant, so a genuine viewer session cannot be produced from the
    // UI. This shapes the identity response to exercise the interface boundary
    // only — the security boundary is enforced by the API and covered by the
    // backend suite.
    await signIn(page);
    await asViewer(page);

    await page.goto(RULES_URL);
    await expect(page.getByRole("heading", { name: "Global Rules" })).toBeVisible();
    await expect(page.getByText("Read only")).toBeVisible();
    await expect(page.getByTestId("new-pricing-rule")).toHaveCount(0);

    await section(page, "Shipping Rules");
    await expect(page.getByTestId("new-shipping-rule")).toHaveCount(0);
  });

  test("a viewer can still preview and read history", async ({ page }) => {
    await signIn(page);
    await page.goto(RULES_URL);
    await createPricingRule(page, { name: "Viewer readable", percent: "50" });

    await asViewer(page);
    await page.reload();

    await section(page, "Live Preview");
    await page.getByLabel("Item cost").fill("10");
    await page.getByLabel("Supplier shipping").fill("4");
    await expect(page.getByTestId("preview-price")).toContainText("21");

    await section(page, "Rule History");
    await expect(page.getByTestId("history-entry").first()).toBeVisible();
  });
});

test.describe("Responsive and theme", () => {
  for (const viewport of [
    { name: "mobile", width: 375, height: 812 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "desktop", width: 1280, height: 800 },
  ]) {
    test(`no horizontal overflow at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await openRules(page);
      await createPricingRule(page, { name: `Sized ${viewport.name}`, percent: "50" });

      for (const name of [
        "Pricing Rules",
        "Shipping Rules",
        "Application Behaviour",
        "Live Preview",
        "Rule History",
      ]) {
        await section(page, name);
        // Polled, not sampled once (review finding J-3): CI failed this on
        // mobile once and passed on retry — a single read straight after
        // switching section can land mid-transition. A layout that really
        // overflows stays overflowing for the whole window and still fails.
        await expect
          .poll(
            () =>
              page.evaluate(
                () =>
                  document.documentElement.scrollWidth >
                  document.documentElement.clientWidth + 1,
              ),
            { message: `${name} overflows at ${viewport.name}`, timeout: 5_000 },
          )
          .toBe(false);
      }
    });
  }

  test("interactive controls meet the minimum target size", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await openRules(page);
    await createPricingRule(page, { name: "Tap target rule", percent: "50" });

    const row = page.getByTestId("rule-row").first();
    for (const name of ["History", "Edit", "Deactivate"]) {
      const box = await row.getByRole("button", { name }).boundingBox();
      expect(box, `${name} has no box`).not.toBeNull();
      expect(box!.height, `${name} is under 40px`).toBeGreaterThanOrEqual(40);
    }
  });

  test("the workspace renders in dark mode", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await openRules(page);
    await expect(page.getByRole("tab", { name: "Pricing Rules" })).toBeVisible();
    const background = await page.evaluate(
      () => getComputedStyle(document.body).backgroundColor,
    );
    expect(background).not.toBe("rgba(0, 0, 0, 0)");
  });

  test("the sections are reachable by keyboard alone", async ({ page }) => {
    await openRules(page);
    await page.getByRole("tab", { name: "Pricing Rules" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Shipping Rules" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.keyboard.press("End");
    await expect(page.getByRole("tab", { name: "Rule History" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.keyboard.press("Home");
    await expect(page.getByRole("tab", { name: "Pricing Rules" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
