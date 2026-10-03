import type { Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";
import { channelsWorld, mockChannelsApi } from "./helpers/channels-fixture";
import { mockAuthResponse } from "./helpers/editor-fixture";

/**
 * Track E4 — team invitations, backend-less. Pins down: an admin's invite
 * sends exactly the address and role; a viewer is not offered the form; the
 * accept page reads the token from the URL fragment and sends it with the
 * legal acceptance, then lands on the dashboard.
 */

const TOKEN = "33333333-3333-4333-8333-333333333333.abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockTeam(page: Parameters<typeof mockChannelsApi>[0], role: "owner" | "viewer") {
  await mockChannelsApi(page, channelsWorld({ role }));
  const sent: unknown[] = [];
  const auth = mockAuthResponse();
  await page.route(/\/api\/v1\/users(\?.*)?$/, (route) =>
    json(route, {
      items: [auth.identity.user],
      meta: { page: 1, size: 100, totalItems: 1, totalPages: 1, hasNext: false, hasPrevious: false },
    }),
  );
  await page.route("**/api/v1/users/invitations", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { email: string; role: string };
      sent.push(body);
      const now = new Date();
      return json(
        route,
        {
          id: "44444444-4444-4444-8444-444444444444",
          email: body.email,
          role: body.role,
          expiresAt: new Date(now.getTime() + 7 * 86_400_000).toISOString(),
          createdAt: now.toISOString(),
          updatedAt: now.toISOString(),
        },
        201,
      );
    }
    return json(route, []);
  });
  return sent;
}

test("an owner invites a colleague with exactly the chosen address and role", async ({ page }) => {
  const sent = await mockTeam(page, "owner");
  await page.goto("/settings/team");
  await page.getByLabel("Email").fill("colleague@example.com");
  await page.getByLabel("Role").selectOption("viewer");
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByRole("status")).toHaveText("Invitation sent to colleague@example.com.");
  expect(sent).toEqual([{ email: "colleague@example.com", role: "viewer" }]);
});

test("a viewer sees the roster but is not offered invitations", async ({ page }) => {
  await mockTeam(page, "viewer");
  await page.goto("/settings/team");
  await expect(page.getByTestId("team-members")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send invitation" })).toHaveCount(0);
});

test("accepting reads the token from the fragment and sends it with the legal acceptance", async ({
  page,
}) => {
  await mockChannelsApi(page, channelsWorld());
  const accepted: Record<string, unknown>[] = [];
  await page.route("**/api/v1/auth/invitations/preview", (route) =>
    json(route, {
      email: "colleague@example.com",
      role: "member",
      workspaceName: "Acme Trading",
      expiresAt: new Date(Date.now() + 86_400_000).toISOString(),
    }),
  );
  await page.route("**/api/v1/auth/invitations/accept", (route) => {
    accepted.push(route.request().postDataJSON() as Record<string, unknown>);
    return json(route, mockAuthResponse(), 201);
  });

  await page.goto(`/invite#token=${TOKEN}`);
  await expect(page.getByRole("heading", { name: "Join Acme Trading" })).toBeVisible();
  const join = page.getByRole("button", { name: "Join workspace" });
  await page.getByLabel("Password", { exact: true }).fill("Correct-Horse-Battery9");
  await page.getByLabel("Confirm password").fill("Correct-Horse-Battery9");
  await expect(join).toBeDisabled();
  await page.getByTestId("accept-legal").check();
  await join.click();

  await expect(page).toHaveURL(/\/dashboard$/);
  expect(accepted).toHaveLength(1);
  expect(accepted[0]).toMatchObject({
    token: TOKEN,
    password: "Correct-Horse-Battery9",
    termsAccepted: true,
    privacyAccepted: true,
  });
});

test("a dead link says so instead of showing the form", async ({ page }) => {
  await mockChannelsApi(page, channelsWorld());
  await page.route("**/api/v1/auth/invitations/preview", (route) =>
    json(route, { code: "not_found", message: "Gone", details: [], requestId: "r" }, 404),
  );
  await page.goto(`/invite#token=${TOKEN}`);
  await expect(page.getByText(/invalid, expired or already used/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Join workspace" })).toHaveCount(0);
});
