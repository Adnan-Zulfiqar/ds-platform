import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { mockAuthResponse } from "./helpers/editor-fixture";
import { mockShellApi } from "./helpers/shell-fixture";

/**
 * The verification email's link lands on a page that consumes the token for
 * the signed-in user. Pins down: the token is read from the fragment and
 * sent exactly once, success refreshes the account, and a used or expired
 * link gets a way back to request another.
 */

const TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789";

/** The shell's mocked user is verified, and a verified user is (rightly)
 * shown the done state without a request. These tests need one who is not. */
async function signInUnverified(page: Page): Promise<void> {
  await mockShellApi(page);
  const auth = mockAuthResponse();
  auth.identity.user.isVerified = false;
  for (const path of ["/auth/me", "/auth/refresh"]) {
    await page.route(`**/api/v1${path}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(path === "/auth/me" ? auth.identity : auth),
      }),
    );
  }
}

test("the link confirms the email and shows the result", async ({ page }) => {
  await signInUnverified(page);
  const bodies: unknown[] = [];
  await page.route("**/api/v1/auth/verify-email/confirm", (route) => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ message: "Email verified." }),
    });
  });

  await page.goto(`/verify-email#token=${TOKEN}`);
  const card = page.getByTestId("verify-email");
  await expect(card).toContainText("Your email address is verified");
  expect(bodies).toEqual([{ token: TOKEN }]);
});

test("an expired link explains itself and points to the profile", async ({
  page,
}) => {
  await signInUnverified(page);
  await page.route("**/api/v1/auth/verify-email/confirm", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({
        code: "not_found",
        message: "Token not found.",
        details: [],
        requestId: "r",
      }),
    }),
  );
  await page.goto(`/verify-email#token=${TOKEN}`);
  const card = page.getByTestId("verify-email");
  await expect(card).toContainText("not valid any more");
  await expect(
    card.getByRole("link", { name: "Go to profile" }),
  ).toHaveAttribute("href", "/settings/profile");
});

test("without a token the page asks for the email link", async ({ page }) => {
  await signInUnverified(page);
  await page.goto("/verify-email");
  await expect(page.getByTestId("verify-email")).toContainText(
    "needs the link",
  );
});

test("an already verified user is told so, with no request", async ({
  page,
}) => {
  await mockShellApi(page); // the shell's user is verified
  const calls: string[] = [];
  await page.route("**/api/v1/auth/verify-email/confirm", (route) => {
    calls.push(route.request().url());
    return route.fulfill({ status: 500, body: "" });
  });
  await page.goto(`/verify-email#token=${TOKEN}`);
  await expect(page.getByTestId("verify-email")).toContainText("is verified");
  expect(calls).toEqual([]);
});
