import type { Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";
import { mockAuthResponse } from "./helpers/editor-fixture";

/**
 * Settings → Profile, backend-less. Regression for the user menu's Profile
 * link, which went to a 404 until this page existed. Pins down: the identity
 * renders, an unverified address can ask for the email, and "sign out
 * everywhere" revokes on the server and then signs this tab out.
 */

function json(route: Route, body: unknown) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}

test("the account menu's Profile link opens a real page with the identity", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld({ role: "admin" }));
  await page.goto("/dashboard");

  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Profile" }).click();

  await expect(page).toHaveURL(/\/settings\/profile$/);
  await expect(page.getByRole("heading", { name: "Profile" })).toBeVisible();
  const details = page.getByTestId("profile-details");
  await expect(details).toContainText("Demo Seller");
  await expect(details).toContainText("ux-l2a-demo@example.com");
  await expect(details).toContainText("Verified");
  await expect(details).toContainText("Demo Workspace");
  await expect(details).toContainText("admin");
  await expect(page.getByRole("button", { name: "Send verification email" })).toHaveCount(0);
});

test("an unverified address can ask for the verification email", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  const auth = mockAuthResponse();
  auth.identity.user.isVerified = false;
  // Registered after the fixture, so these take precedence for the session.
  await page.route("**/api/v1/auth/refresh", (route) => json(route, auth));
  await page.route("**/api/v1/auth/me", (route) => json(route, auth.identity));
  let requests = 0;
  await page.route("**/api/v1/auth/verify-email/request", (route) => {
    requests += 1;
    return json(route, { message: "prepared" });
  });

  await page.goto("/settings/profile");
  await expect(page.getByTestId("profile-details")).toContainText("Not verified");
  await page.getByRole("button", { name: "Send verification email" }).click();

  await expect(page.getByRole("status")).toHaveText("Check your inbox for the verification link.");
  expect(requests).toBe(1);
});

test("sign out everywhere revokes on the server and returns to the login page", async ({
  page,
}) => {
  await mockChannelsApi(page, channelsWorld());
  let revoked = 0;
  await page.route("**/api/v1/auth/logout-all", (route) => {
    revoked += 1;
    return json(route, { message: "Signed out of 3 session(s)." });
  });

  await page.goto("/settings/profile");
  await page.getByRole("button", { name: "Sign out everywhere" }).click();

  await page.waitForURL(/\/login/);
  expect(revoked).toBe(1);
});

test("the settings index links to the profile and the password link is real", async ({
  page,
}) => {
  await mockChannelsApi(page, channelsWorld());
  await page.goto("/settings");
  await expect(page.getByRole("link", { name: /Profile/ })).toHaveAttribute(
    "href",
    "/settings/profile",
  );
  await page.goto("/settings/profile");
  await expect(page.getByRole("link", { name: "Change password" })).toHaveAttribute(
    "href",
    "/forgot-password",
  );
});
