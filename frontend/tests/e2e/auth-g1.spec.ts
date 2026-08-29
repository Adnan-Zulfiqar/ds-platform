import { expect, test, type Page } from "@playwright/test";

import { isApiReachable } from "./helpers/auth";

/**
 * AUTH-G1 — Google sign-in and password reset, end to end in the browser.
 *
 * **Google's script is never loaded.** `accounts.google.com/gsi/client` is
 * intercepted and replaced with a stub that draws a button and hands back a
 * fixed credential string. Loading the real thing would contact Google from a
 * test run, and the credential it returned could not be verified by the
 * isolated backend anyway.
 *
 * That boundary is deliberate and worth being explicit about: these tests prove
 * the *application's* behaviour around the button — that it appears, that the
 * credential is posted, that a conflict is explained, that nothing leaks into
 * the DOM or storage. They do **not** prove a real Google sign-in works. Nothing
 * here should be read as evidence that it does.
 */

const GSI_URL = "https://accounts.google.com/gsi/client";

/**
 * Replace Google's script with one that renders a real button element and
 * invokes the callback when it is clicked.
 */
async function stubGoogleScript(page: Page, credential = "stubbed.google.credential") {
  await page.route(`${GSI_URL}*`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: `
        window.google = { accounts: { id: {
          initialize: (config) => { window.__gsiConfig = config; },
          renderButton: (parent) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.setAttribute('data-testid', 'stub-google-button');
            b.textContent = 'Continue with Google';
            b.addEventListener('click', () => {
              window.__gsiConfig.callback({ credential: ${JSON.stringify(credential)} });
            });
            parent.appendChild(b);
          },
        } } };
      `,
    }),
  );
}

test.describe("Google sign-in button", () => {
  test.beforeEach(async ({ page }) => {
    await stubGoogleScript(page);
  });

  for (const route of ["/login", "/register"]) {
    test(`is offered on ${route}`, async ({ page }) => {
      test.skip(!(await isApiReachable()), "Backend API is not reachable.");

      await page.goto(route);

      await expect(page.getByTestId("google-sign-in")).toBeVisible();
      await expect(page.getByTestId("stub-google-button")).toBeVisible({ timeout: 15000 });
    });
  }

  test("requests a nonce before drawing the button", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    const calls: string[] = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (path.includes("/auth/google")) calls.push(`${r.method()} ${path}`);
    });

    await page.goto("/login");
    await expect(page.getByTestId("stub-google-button")).toBeVisible({ timeout: 15000 });

    // The nonce is what makes a captured credential unusable later.
    expect(calls).toContain("POST /api/v1/auth/google/nonce");
  });

  test("posts the credential to the backend and signs in", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    await page.goto("/login");
    await expect(page.getByTestId("stub-google-button")).toBeVisible({ timeout: 15000 });

    // Armed before the click: `waitForRequest` only sees requests made after it
    // is called, and this one fires immediately.
    const pending = page.waitForRequest(
      (r) =>
        r.url().includes("/api/v1/auth/google") &&
        !r.url().includes("/nonce") &&
        r.method() === "POST",
      { timeout: 15000 },
    );
    await page.getByTestId("stub-google-button").click();
    const posted = await pending;
    const body = posted.postDataJSON() as { credential?: string; nonce?: string };
    expect(body.credential).toBe("stubbed.google.credential");
    // The nonce travels with it, so the backend can tell this attempt apart.
    expect(body.nonce).toBeTruthy();
  });

  test("explains an account-linking conflict instead of failing silently", async ({
    page,
  }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    // The refusal the backend returns when the address already has a local
    // account. The person can act on it, so it must be shown.
    // Only the sign-in endpoint. The glob must not swallow `/google/nonce`,
    // or the button never renders and the test passes for the wrong reason.
    await page.route("**/api/v1/auth/google", (route) => {
      if (new URL(route.request().url()).pathname.endsWith("/auth/google")) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code: "google_account_requires_linking", message: "Already exists." },
          }),
        });
      }
      return route.continue();
    });

    await page.goto("/login");
    await page.getByTestId("stub-google-button").click({ timeout: 15000 });

    // Targeted by id rather than by role: Next.js renders its own route
    // announcer with `role="alert"`, and shadcn's Alert sets the role too, so
    // the role alone matches several elements.
    const alert = page.getByTestId("google-error");
    await expect(alert).toBeVisible({ timeout: 15000 });
    await expect(alert).toContainText(/sign in with your password/i);
  });

  test("puts no credential in the DOM, the URL or browser storage", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    await page.goto("/login");
    await page.getByTestId("stub-google-button").click({ timeout: 15000 });
    await page.waitForTimeout(2000);

    expect(page.url()).not.toContain("credential");
    expect(await page.content()).not.toContain("stubbed.google.credential");

    const stored = await page.evaluate(() => {
      const dump = (s: Storage) =>
        Object.keys(s)
          .map((k) => `${k}=${s.getItem(k)}`)
          .join("|");
      try {
        return `${dump(localStorage)}||${dump(sessionStorage)}`;
      } catch {
        return "";
      }
    });
    expect(stored).not.toContain("stubbed.google.credential");
  });
});

test.describe("Password reset — three steps", () => {
  test("walks address, code and new password", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    await page.route("**/api/v1/auth/password-reset/request", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          challengeId: "challenge-1",
          expiresInSeconds: 600,
          message: "If that address has an account, a six-digit code is on its way.",
        }),
      }),
    );
    await page.route("**/api/v1/auth/password-reset/verify", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ resetTicket: "ticket-1", expiresInSeconds: 600 }),
      }),
    );
    await page.route("**/api/v1/auth/password-reset/complete", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ message: "Password updated." }),
      }),
    );

    await page.goto("/forgot-password");

    await page.getByTestId("reset-email").fill("person@example.com");
    await page.getByRole("button", { name: "Send code" }).click();

    await expect(page.getByTestId("reset-code")).toBeVisible();
    await page.getByTestId("reset-code").fill("123456");
    await page.getByRole("button", { name: "Verify code" }).click();

    await expect(page.getByTestId("reset-password")).toBeVisible();
    await page.getByTestId("reset-password").fill("An0ther-Strong-Pass!");
    await page.getByTestId("reset-confirm").fill("An0ther-Strong-Pass!");
    await page.getByRole("button", { name: "Set new password" }).click();

    // The success state appears only after the backend confirms.
    await expect(page.getByTestId("reset-done")).toBeVisible();
    await expect(page.getByTestId("reset-done")).toContainText(/signed out/i);
  });

  test("says the same thing for an unknown address", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    // The real endpoint returns a challenge either way; the page must advance
    // rather than say "no account found", which would be a membership list.
    await page.goto("/forgot-password");
    await page.getByTestId("reset-email").fill(`nobody-${Date.now()}@example.com`);
    await page.getByRole("button", { name: "Send code" }).click();

    await expect(page.getByTestId("reset-code")).toBeVisible({ timeout: 15000 });
    const notice = await page.getByTestId("reset-notice").innerText();
    expect(notice).toMatch(/if that address has an account/i);
    expect(notice).not.toMatch(/not found|no account exists|unknown/i);
  });

  test("reports a wrong code without saying which part was wrong", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    await page.route("**/api/v1/auth/password-reset/request", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          challengeId: "c", expiresInSeconds: 600, message: "On its way.",
        }),
      }),
    );
    await page.route("**/api/v1/auth/password-reset/verify", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "authentication_failed", message: "That code is not valid." },
        }),
      }),
    );

    await page.goto("/forgot-password");
    await page.getByTestId("reset-email").fill("person@example.com");
    await page.getByRole("button", { name: "Send code" }).click();
    await page.getByTestId("reset-code").fill("000000");
    await page.getByRole("button", { name: "Verify code" }).click();

    const alert = page.getByTestId("reset-error");
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(/not valid/i);
    // Expiry, wrong code and exhausted attempts are one message.
    await expect(alert).not.toContainText(/expired|attempts remaining/i);
  });

  test("counts down before another code can be sent", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    await page.route("**/api/v1/auth/password-reset/request", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          challengeId: "c", expiresInSeconds: 600, message: "On its way.",
        }),
      }),
    );

    await page.goto("/forgot-password");
    await page.getByTestId("reset-email").fill("person@example.com");
    await page.getByRole("button", { name: "Send code" }).click();

    const resend = page.getByTestId("reset-resend");
    await expect(resend).toBeVisible();
    await expect(resend).toBeDisabled();
    await expect(resend).toContainText(/Resend in \d+s/);
  });

  test("never puts the code in the URL", async ({ page }) => {
    test.skip(!(await isApiReachable()), "Backend API is not reachable.");

    await page.goto("/forgot-password");
    await page.getByTestId("reset-email").fill("person@example.com");
    await page.getByRole("button", { name: "Send code" }).click();
    await page.waitForTimeout(1500);

    expect(page.url()).not.toMatch(/\d{6}/);
    expect(page.url()).not.toContain("challenge");
  });
});

test.describe("Password reset — presentation", () => {
  test("is operable by keyboard alone", async ({ page }) => {
    await page.goto("/forgot-password");

    const email = page.getByTestId("reset-email");
    await email.focus();
    expect(await email.evaluate((el) => el === document.activeElement)).toBe(true);
  });

  test("announces progress in a live region", async ({ page }) => {
    await page.goto("/forgot-password");

    const status = page.getByTestId("reset-status");
    await expect(status).toHaveAttribute("aria-live", "polite");
  });

  test("renders in dark mode", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/forgot-password");

    await expect(page.getByRole("heading", { name: /Reset your password/i })).toBeVisible();
  });

  for (const [label, width] of [
    ["mobile", 375],
    ["desktop", 1440],
  ] as const) {
    test(`does not overflow horizontally at ${label}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/forgot-password");

      const overflows = await page.evaluate(
        () =>
          document.documentElement.scrollWidth > document.documentElement.clientWidth,
      );
      expect(overflows).toBe(false);
    });
  }
});
