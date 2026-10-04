import type { Page, Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";

/**
 * Track E5d — the platform-operator console, backend-less (D-015). Pins
 * down: sign-in sends email, password and code to the platform API with no
 * tenant token; the workspace list, health and audit render; suspending
 * sends exactly the reason; and a switched-off panel says so.
 */

const TENANT = {
  id: "12121212-1212-4121-8121-121212121212",
  name: "Acme Trading",
  slug: "acme-trading",
  status: "active",
  isActive: true,
  createdAt: new Date().toISOString(),
  users: 3,
  connectedStores: 2,
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockPlatform(page: Page) {
  const log: { method: string; path: string; body: unknown; auth: string | null }[] = [];
  let tenant = { ...TENANT };
  await page.route("**/api/v1/platform/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^.*\/api\/v1\/platform/, "");
    log.push({
      method: request.method(),
      path,
      body: request.postDataJSON?.() ?? null,
      auth: request.headers()["authorization"] ?? null,
    });
    if (path === "/auth/login") {
      return json(route, { accessToken: "platform-token", expiresAt: new Date().toISOString() });
    }
    if (path === "/tenants") {
      return json(route, {
        items: [tenant],
        meta: { page: 1, size: 25, totalItems: 1, totalPages: 1, hasNext: false, hasPrevious: false },
      });
    }
    if (path === `/tenants/${TENANT.id}/health`) {
      return json(route, {
        tenantId: TENANT.id,
        windowHours: 24,
        failedOrderSyncs: 1,
        failedInventorySyncs: 0,
        listingsInError: 2,
        failedNotificationEmails: 0,
      });
    }
    if (path === `/tenants/${TENANT.id}/suspend`) {
      tenant = { ...tenant, isActive: false, status: "suspended" };
      return json(route, tenant);
    }
    if (path === "/audit") {
      return json(route, [
        {
          id: "a1",
          createdAt: new Date().toISOString(),
          adminId: "x",
          action: "login_succeeded",
          targetTenantId: null,
          detail: {},
          clientIp: "127.0.0.1",
        },
      ]);
    }
    return json(route, { code: "not_mocked", message: path }, 404);
  });
  return log;
}

test("an operator signs in, inspects a workspace and suspends it with a reason", async ({
  page,
}) => {
  const log = await mockPlatform(page);
  await page.goto("/platform");

  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("Correct-Horse-Battery9");
  await page.getByLabel("One-time code").fill("123456");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByTestId("platform-tenants")).toContainText("Acme Trading");
  const login = log.find((entry) => entry.path === "/auth/login");
  expect(login?.body).toEqual({
    email: "ops@example.com",
    password: "Correct-Horse-Battery9",
    code: "123456",
  });
  expect(login?.auth).toBeNull();
  expect(log.find((entry) => entry.path === "/tenants")?.auth).toBe("Bearer platform-token");
  await expect(page.getByTestId("platform-audit")).toContainText("login_succeeded");

  await page.getByRole("button", { name: /Acme Trading/ }).click();
  const panel = page.getByTestId("platform-tenant-panel");
  await expect(panel.getByTestId("platform-tenant-health")).toContainText("Listings in error: 2");
  const suspend = panel.getByRole("button", { name: "Suspend workspace" });
  await expect(suspend).toBeDisabled();
  await panel.getByLabel(/Reason/).fill("chargeback fraud");
  await suspend.click();

  await expect(panel.getByRole("button", { name: "Reactivate workspace" })).toBeVisible();
  expect(log.find((entry) => entry.path.endsWith("/suspend"))?.body).toEqual({
    reason: "chargeback fraud",
  });
});

test("a switched-off console says so instead of a generic failure", async ({ page }) => {
  await page.route("**/api/v1/platform/**", (route) =>
    json(route, { code: "not_found", message: "Not found", details: [], requestId: "r" }, 404),
  );
  await page.goto("/platform");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("x");
  await page.getByLabel("One-time code").fill("123456");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("not enabled for this network");
});
