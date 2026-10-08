import { expect, test } from "./fixtures/provider-isolation";
import {
  NOW,
  SESSION,
  TENANT_ID,
  confirmReauth,
  goTo,
  mockPlatform,
  overview,
  signIn,
} from "./platform-mock";

/**
 * Track E5d / D-018 / D-019 — the platform-operator console, backend-less.
 * Pins down:
 * - sign-in sends email, password and code with no tenant token;
 * - the console follows the permissions `/me` reports;
 * - a `reauth_required` refusal asks for password and a new code, then retries;
 * - suspending sends exactly the reason;
 * - operator management and session ending call the right routes;
 * - a switched-off panel says so.
 */

const COLLEAGUE = {
  id: "op2",
  email: "colleague@example.com",
  role: "admin",
  isActive: true,
  createdAt: NOW,
  lastLoginAt: null,
  openSessions: 1,
};
const OTHER_SESSION = {
  ...SESSION,
  id: "s2",
  clientIp: "198.51.100.7",
  current: false,
};

test("an operator lands on the live dashboard after signing in", async ({
  page,
}) => {
  const log = await mockPlatform(page);
  await signIn(page);

  const login = log.find((entry) => entry.path === "/auth/login");
  expect(login?.body).toEqual({
    email: "ops@example.com",
    password: "Correct-Horse-Battery9",
    code: "123456",
  });
  expect(login?.auth).toBeNull();
  await expect(page.getByTestId("platform-operator-role")).toHaveText(
    "Super admin",
  );
  const stats = page.getByTestId("platform-dashboard-stats");
  await expect(stats).toContainText("10"); // 7 active + 3 trial workspaces
  await expect(stats).toContainText("21");
  await expect(page.getByTestId("platform-dashboard-failures")).toContainText(
    "2 in total",
  );
  await expect(page.getByTestId("platform-dashboard-system")).toContainText(
    "Schema 0053",
  );
  expect(log.find((e) => e.path === "/dashboard")?.auth).toBe(
    "Bearer platform-token",
  );
});

test("an operator opens a workspace, re-authenticates and suspends it with a reason", async ({
  page,
}) => {
  let active = true;
  const log = await mockPlatform(page, {
    reauthPaths: [`/tenants/${TENANT_ID}/suspend`],
    handlers: {
      [`GET /workspaces/${TENANT_ID}`]: () => overview(active),
      [`POST /tenants/${TENANT_ID}/suspend`]: () => {
        active = false;
        return {};
      },
    },
  });
  await signIn(page);
  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();

  const panel = page.getByTestId("platform-tenant-panel");
  await expect(page.getByTestId("platform-tenant-health")).toContainText("2");
  const suspend = panel.getByRole("button", { name: "Suspend workspace" });
  await expect(suspend).toBeDisabled();
  await panel.getByLabel(/Reason/).fill("chargeback fraud");
  await suspend.click();
  await confirmReauth(page);

  await expect(
    panel.getByRole("button", { name: "Reactivate workspace" }),
  ).toBeVisible();
  const suspends = log.filter((entry) => entry.path.endsWith("/suspend"));
  expect(suspends).toHaveLength(2); // refused, then retried after re-authentication
  expect(suspends[1]?.body).toEqual({ reason: "chargeback fraud" });
  expect(log.find((e) => e.path === "/auth/reauth")?.body).toEqual({
    password: "Correct-Horse-Battery9",
    code: "654321",
  });
});

test("a finance operator sees only what their role allows", async ({
  page,
}) => {
  await mockPlatform(page, {
    role: "finance",
    permissions: [
      "billing.manage",
      "billing.read",
      "dashboard.read",
      "tenants.read",
    ],
  });
  await signIn(page);

  const nav = page.getByRole("navigation", { name: "Console sections" });
  await expect(nav.getByRole("link", { name: "Workspaces" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Operators" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Audit log" })).toHaveCount(0);

  await goTo(page, "Workspaces");
  await page.getByRole("link", { name: /Acme Trading/ }).click();
  await expect(
    page.getByText("Your role cannot see inside workspaces."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /workspace/ })).toHaveCount(0);
});

test("a super admin changes a colleague's role after re-authenticating", async ({
  page,
}) => {
  const log = await mockPlatform(page, {
    reauthPaths: ["/operators/op2/role"],
    handlers: {
      "GET /operators": () => [
        {
          ...COLLEAGUE,
          id: "op1",
          email: "ops@example.com",
          role: "super_admin",
        },
        COLLEAGUE,
      ],
      "GET /operators/op2/sessions": () => [OTHER_SESSION],
      "POST /operators/op2/role": () => ({
        ...COLLEAGUE,
        role: "auditor",
        openSessions: 0,
      }),
    },
  });
  await signIn(page);
  await goTo(page, "Operators");

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
  await mockPlatform(page, {
    handlers: {
      "GET /operators": () => [
        {
          ...COLLEAGUE,
          id: "op1",
          email: "ops@example.com",
          role: "super_admin",
        },
      ],
      "GET /operators/op1/sessions": () => [SESSION],
    },
  });
  await signIn(page);
  await goTo(page, "Operators");
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
  const log = await mockPlatform(page, {
    handlers: {
      "GET /auth/sessions": () => [SESSION, OTHER_SESSION],
      "POST /auth/sessions/s2/revoke": () => ({ status: 204 }),
    },
  });
  await signIn(page);
  await goTo(page, "My sessions");

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
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({
        code: "not_found",
        message: "Not found",
        details: [],
        requestId: "r",
      }),
    }),
  );
  await page.goto("/platform");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("x");
  await page.getByLabel("One-time code").fill("123456");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("not enabled for this network")).toBeVisible();
});
