import type { Page, Route } from "@playwright/test";

import { expect, test } from "./fixtures/provider-isolation";

/**
 * Track E5d / D-018 — the platform-operator console, backend-less. Pins down:
 * sign-in sends email, password and code with no tenant token; the console
 * follows the permissions `/me` reports; a `reauth_required` refusal asks for
 * password and a new code and then retries; suspending sends exactly the
 * reason; operator management and session ending call the right routes; and
 * a switched-off panel says so.
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

const NOW = new Date().toISOString();
const SESSION = {
  id: "s1",
  createdAt: NOW,
  expiresAt: NOW,
  lastSeenAt: NOW,
  reauthenticatedAt: null,
  clientIp: "127.0.0.1",
  userAgent: "Playwright",
  current: true,
};
const OTHER_SESSION = {
  ...SESSION,
  id: "s2",
  clientIp: "198.51.100.7",
  current: false,
};
const ALL = [
  "audit.read",
  "operators.manage",
  "operators.read",
  "tenants.read",
  "tenants.suspend",
];
const COLLEAGUE = {
  id: "op2",
  email: "colleague@example.com",
  role: "admin",
  isActive: true,
  createdAt: NOW,
  lastLoginAt: null,
  openSessions: 1,
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockPlatform(
  page: Page,
  {
    role = "super_admin",
    permissions = ALL,
  }: { role?: string; permissions?: string[] } = {},
) {
  const log: {
    method: string;
    path: string;
    body: unknown;
    auth: string | null;
  }[] = [];
  let tenant = { ...TENANT };
  let reauthenticated = false;
  await page.route("**/api/v1/platform/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(
      /^.*\/api\/v1\/platform/,
      "",
    );
    log.push({
      method: request.method(),
      path,
      body: request.postDataJSON?.() ?? null,
      auth: request.headers()["authorization"] ?? null,
    });
    const needsReauth = () =>
      json(
        route,
        {
          code: "reauth_required",
          message: "Confirm",
          details: [],
          requestId: "r",
        },
        403,
      );

    if (path === "/auth/login") {
      return json(route, { accessToken: "platform-token", expiresAt: NOW });
    }
    if (path === "/me") {
      return json(route, {
        id: "op1",
        email: "ops@example.com",
        role,
        permissions,
        lastLoginAt: NOW,
        session: SESSION,
        reauthValidUntil: null,
      });
    }
    if (path === "/auth/reauth") {
      reauthenticated = true;
      return json(route, { reauthenticatedAt: NOW, validUntil: NOW });
    }
    if (path === "/auth/logout") return route.fulfill({ status: 204 });
    if (path === "/auth/sessions") return json(route, [SESSION, OTHER_SESSION]);
    if (path === "/auth/sessions/s2/revoke")
      return route.fulfill({ status: 204 });
    if (path === "/tenants") {
      return json(route, {
        items: [tenant],
        meta: {
          page: 1,
          size: 25,
          totalItems: 1,
          totalPages: 1,
          hasNext: false,
          hasPrevious: false,
        },
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
      if (!reauthenticated) return needsReauth();
      tenant = { ...tenant, isActive: false, status: "suspended" };
      return json(route, tenant);
    }
    if (path === "/operators") {
      return json(route, [
        {
          ...COLLEAGUE,
          id: "op1",
          email: "ops@example.com",
          role,
          openSessions: 2,
        },
        COLLEAGUE,
      ]);
    }
    if (path === "/operators/op2/sessions") return json(route, [OTHER_SESSION]);
    if (path === "/operators/op2/role") {
      if (!reauthenticated) return needsReauth();
      return json(route, { ...COLLEAGUE, role: "auditor", openSessions: 0 });
    }
    if (path === "/audit") {
      return json(route, [
        {
          id: "a1",
          createdAt: NOW,
          adminId: "op1",
          actorRole: "super_admin",
          action: "login_succeeded",
          outcome: "success",
          targetTenantId: null,
          targetType: "session",
          targetId: "s1",
          detail: {},
          clientIp: "127.0.0.1",
          userAgent: "Playwright",
          requestId: "r1",
        },
      ]);
    }
    return json(route, { code: "not_mocked", message: path }, 404);
  });
  return log;
}

async function signIn(page: Page) {
  await page.goto("/platform");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("Correct-Horse-Battery9");
  await page.getByLabel("One-time code").fill("123456");
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function confirmReauth(page: Page) {
  const dialog = page.getByTestId("platform-reauth-dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Password").fill("Correct-Horse-Battery9");
  await dialog.getByLabel("New one-time code").fill("654321");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog).toBeHidden();
}

test("an operator signs in, re-authenticates and suspends a workspace with a reason", async ({
  page,
}) => {
  const log = await mockPlatform(page);
  await signIn(page);

  await expect(page.getByTestId("platform-tenants")).toContainText(
    "Acme Trading",
  );
  await expect(page.getByTestId("platform-operator-role")).toHaveText(
    "Super admin",
  );
  const login = log.find((entry) => entry.path === "/auth/login");
  expect(login?.body).toEqual({
    email: "ops@example.com",
    password: "Correct-Horse-Battery9",
    code: "123456",
  });
  expect(login?.auth).toBeNull();
  expect(log.find((entry) => entry.path === "/tenants")?.auth).toBe(
    "Bearer platform-token",
  );

  await page.getByRole("button", { name: /Acme Trading/ }).click();
  const panel = page.getByTestId("platform-tenant-panel");
  await expect(panel.getByTestId("platform-tenant-health")).toContainText(
    "Listings in error: 2",
  );
  const suspend = panel.getByRole("button", { name: "Suspend workspace" });
  await expect(suspend).toBeDisabled();
  await panel.getByLabel(/Reason/).fill("chargeback fraud");
  await suspend.click();

  await confirmReauth(page);
  await expect(
    panel.getByRole("button", { name: "Reactivate workspace" }),
  ).toBeVisible();
  expect(log.find((entry) => entry.path === "/auth/reauth")?.body).toEqual({
    password: "Correct-Horse-Battery9",
    code: "654321",
  });
  const suspends = log.filter((entry) => entry.path.endsWith("/suspend"));
  expect(suspends).toHaveLength(2); // refused, then retried after re-authentication
  expect(suspends[1]?.body).toEqual({ reason: "chargeback fraud" });

  await page.getByRole("button", { name: "Audit log" }).click();
  await expect(page.getByTestId("platform-audit")).toContainText(
    "login_succeeded",
  );
});

test("a support operator sees only what their role allows", async ({
  page,
}) => {
  await mockPlatform(page, { role: "support", permissions: ["tenants.read"] });
  await signIn(page);

  await expect(page.getByTestId("platform-operator-role")).toHaveText(
    "Support",
  );
  const nav = page.getByRole("navigation", { name: "Console sections" });
  await expect(nav.getByRole("button", { name: "Workspaces" })).toBeVisible();
  await expect(nav.getByRole("button", { name: "Operators" })).toHaveCount(0);
  await expect(nav.getByRole("button", { name: "Audit log" })).toHaveCount(0);

  await page.getByRole("button", { name: /Acme Trading/ }).click();
  const panel = page.getByTestId("platform-tenant-panel");
  await expect(panel.getByTestId("platform-tenant-health")).toBeVisible();
  await expect(panel.getByRole("button", { name: /workspace/ })).toHaveCount(0);
});

test("a super admin changes a colleague's role after re-authenticating", async ({
  page,
}) => {
  const log = await mockPlatform(page);
  await signIn(page);
  await page.getByRole("button", { name: "Operators" }).click();

  await page.getByRole("button", { name: /colleague@example.com/ }).click();
  const panel = page.getByTestId("platform-operator-panel");
  await expect(panel.getByTestId("platform-operator-sessions")).toContainText(
    "198.51.100.7",
  );
  await panel.getByLabel(/Reason/).fill("moved to compliance");
  await panel.getByLabel("Role").selectOption("auditor");
  await panel.getByRole("button", { name: "Change role" }).click();
  await confirmReauth(page);

  await expect
    .poll(() => log.filter((e) => e.path === "/operators/op2/role").length)
    .toBe(2);
  expect(log.filter((e) => e.path === "/operators/op2/role")[1]?.body).toEqual({
    role: "auditor",
    reason: "moved to compliance",
  });
});

test("an operator cannot manage themselves in the console", async ({
  page,
}) => {
  await mockPlatform(page);
  await signIn(page);
  await page.getByRole("button", { name: "Operators" }).click();
  await page.getByRole("button", { name: /ops@example.com/ }).click();
  const panel = page.getByTestId("platform-operator-panel");
  await expect(panel).toContainText("You cannot change your own role");
  await expect(panel.getByRole("button", { name: "Change role" })).toHaveCount(
    0,
  );
});

test("an operator ends another session and signs out on the server", async ({
  page,
}) => {
  const log = await mockPlatform(page);
  await signIn(page);
  await page.getByRole("button", { name: "My sessions" }).click();

  const sessions = page.getByTestId("platform-my-sessions");
  await expect(sessions).toContainText("(this session)");
  await sessions.getByRole("button", { name: "End session" }).click();
  await expect
    .poll(() => log.some((e) => e.path === "/auth/sessions/s2/revoke"))
    .toBe(true);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  expect(log.find((e) => e.path === "/auth/logout")?.auth).toBe(
    "Bearer platform-token",
  );
});

test("a switched-off console says so instead of a generic failure", async ({
  page,
}) => {
  await page.route("**/api/v1/platform/**", (route) =>
    json(
      route,
      { code: "not_found", message: "Not found", details: [], requestId: "r" },
      404,
    ),
  );
  await page.goto("/platform");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("x");
  await page.getByLabel("One-time code").fill("123456");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("not enabled for this network")).toBeVisible();
});
