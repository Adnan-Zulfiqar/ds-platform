import { expect, test } from "@playwright/test";

import { acceptLegal } from "./helpers/auth";

/**
 * Authentication UI tests.
 *
 * These assert on what a user sees and can do — labels, validation messages,
 * redirects — rather than on component internals, so a restyle does not break
 * them and a genuine regression does.
 *
 * They deliberately do not require a running backend. Rendering, client-side
 * validation, and route protection are all client concerns; the flows that need
 * a server are covered by the backend integration suite, which drives the real
 * API rather than a mocked one.
 */

test.describe("Login page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
  });

  test("renders the sign-in form", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  });

  test("offers remember me and forgot password", async ({ page }) => {
    await expect(page.getByLabel("Remember me on this device")).toBeVisible();
    await expect(page.getByRole("link", { name: "Forgot password?" })).toBeVisible();
  });

  test("links to registration", async ({ page }) => {
    await page.getByRole("link", { name: "Create one" }).click();
    await expect(page).toHaveURL(/\/register$/);
  });

  test("requires both fields", async ({ page }) => {
    // `noValidate` is set on the form, so these come from Zod rather than from
    // the browser's native validation — which is the behaviour we want to test.
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await expect(page.getByText("Email address is required")).toBeVisible();
    await expect(page.getByText("Password is required")).toBeVisible();
  });

  test("rejects a malformed email address", async ({ page }) => {
    await page.getByLabel("Email").fill("not-an-email");
    await page.getByLabel("Password").fill("something");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await expect(page.getByText("Enter a valid email address")).toBeVisible();
  });

  test("does not apply the full password policy on sign-in", async ({ page }) => {
    /**
     * A sign-in form must accept any non-empty password. Enforcing the current
     * policy here would reject a legitimate password set before the rules
     * changed, and would tell an attacker what the rules are for free.
     */
    await page.getByLabel("Email").fill("user@example.com");
    await page.getByLabel("Password").fill("short");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await expect(page.getByText(/must be at least 12 characters/i)).toHaveCount(0);
  });
});

test.describe("Registration page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/register");
  });

  test("renders every required field", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
    await expect(page.getByLabel("Company name")).toBeVisible();
    await expect(page.getByLabel("First name")).toBeVisible();
    await expect(page.getByLabel("Last name")).toBeVisible();
    await expect(page.getByLabel("Work email")).toBeVisible();
    await expect(page.getByLabel("Password", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Confirm password")).toBeVisible();
  });

  test("enforces the password policy", async ({ page }) => {
    await page.getByLabel("Company name").fill("Acme Trading");
    await page.getByLabel("Work email").fill("owner@acme.example");
    await page.getByLabel("Password", { exact: true }).fill("weak");
    await page.getByLabel("Confirm password").fill("weak");
    // Acceptance now gates the submit control, so the test does what a
    // person must do before the button is usable.
    await acceptLegal(page);
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByText(/at least 12 characters/i).first()).toBeVisible();
  });

  test("reports mismatched passwords against the confirmation field", async ({
    page,
  }) => {
    await page.getByLabel("Company name").fill("Acme Trading");
    await page.getByLabel("Work email").fill("owner@acme.example");
    await page.getByLabel("Password", { exact: true }).fill("Correct-Horse-Battery9");
    await page.getByLabel("Confirm password").fill("Different-Horse-Battery9");
    // Acceptance now gates the submit control, so the test does what a
    // person must do before the button is usable.
    await acceptLegal(page);
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page.getByText("Passwords do not match")).toBeVisible();
  });

  test("shows a password strength indicator as the user types", async ({ page }) => {
    const password = page.getByLabel("Password", { exact: true });

    await password.fill("abc");
    await expect(page.getByText(/Password strength:/)).toBeVisible();

    await password.fill("Correct-Horse-Battery9!");
    await expect(page.getByText(/Password strength: Strong/)).toBeVisible();
  });

  test("requires a company name", async ({ page }) => {
    // Acceptance now gates the submit control, so the test does what a
    // person must do before the button is usable.
    await acceptLegal(page);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByText("Company name is required")).toBeVisible();
  });
});

test.describe("Forgot password page", () => {
  /**
   * This used to assert the page said reset was unavailable, which was the
   * honest thing to show while no backend existed. AUTH-G1 implemented it, so
   * the assertion was correctly invalidated and is replaced by the property
   * that still matters: the page must never fake a confirmation.
   *
   * The full three-step flow is covered in `auth-g1.spec.ts`.
   */
  test("asks for an address and claims nothing before the backend answers", async ({
    page,
  }) => {
    await page.goto("/forgot-password");

    await expect(page.getByTestId("reset-email")).toBeVisible();
    // Nothing about a sent email until a request has actually been made.
    await expect(page.getByTestId("reset-notice")).toHaveCount(0);
    await expect(page.getByTestId("reset-done")).toHaveCount(0);
    await expect(page.getByText(/Not available yet/i)).toHaveCount(0);
  });
});

test.describe("Route protection", () => {
  test("redirects an unauthenticated visitor away from the dashboard", async ({
    page,
  }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/);
  });

  test("preserves the intended destination", async ({ page }) => {
    // So the user lands where they were going after signing in, rather than
    // always on the dashboard.
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/next=/);
  });

  test("root redirects through to sign-in when signed out", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });

  test("public routes remain reachable", async ({ page }) => {
    for (const route of ["/login", "/register", "/forgot-password"]) {
      await page.goto(route);
      await expect(page).toHaveURL(new RegExp(`${route}$`));
    }
  });
});

test.describe("Accessibility", () => {
  test("form fields are associated with their labels", async ({ page }) => {
    await page.goto("/login");

    // getByLabel resolving at all proves the label/control association, which
    // is what a screen reader depends on.
    await expect(page.getByLabel("Email")).toBeEditable();
    await expect(page.getByLabel("Password")).toBeEditable();
  });

  test("validation errors are announced", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    // role="alert" is what makes the failure announced rather than silently
    // rendered.
    await expect(page.getByRole("alert").first()).toBeVisible();
  });

  test("invalid fields are marked for assistive technology", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await expect(page.getByLabel("Email")).toHaveAttribute("aria-invalid", "true");
  });
});
