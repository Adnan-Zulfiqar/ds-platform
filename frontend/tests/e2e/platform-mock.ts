import type { Page, Route } from "@playwright/test";

import { expect } from "./fixtures/provider-isolation";

/**
 * A mocked platform API for the operator-console specs (D-018, D-019).
 *
 * Routes answer from `handlers` (exact path, or "METHOD path"); anything
 * unhandled is a 404 the test can see in `log`. Actions listed in
 * `reauthPaths` answer 403 `reauth_required` until `/auth/reauth` is called,
 * as the server does.
 */

export const NOW = new Date().toISOString();
export const TENANT_ID = "12121212-1212-4121-8121-121212121212";

export const ALL_PERMISSIONS = [
  "audit.export",
  "audit.read",
  "billing.manage",
  "billing.read",
  "catalog.manage",
  "dashboard.read",
  "jobs.manage",
  "jobs.read",
  "operators.manage",
  "operators.read",
  "orders.manage",
  "settings.manage",
  "stores.manage",
  "support.session",
  "tenants.read",
  "tenants.suspend",
  "users.manage",
  "workspace.data.read",
];

export const SESSION = {
  id: "s1",
  createdAt: NOW,
  expiresAt: NOW,
  lastSeenAt: NOW,
  reauthenticatedAt: null,
  clientIp: "127.0.0.1",
  userAgent: "Playwright",
  current: true,
};

export const TENANT = {
  id: TENANT_ID,
  name: "Acme Trading",
  slug: "acme-trading",
  status: "active",
  isActive: true,
  createdAt: NOW,
  users: 3,
  connectedStores: 2,
};

export const page1 = <T>(items: T[]) => ({
  items,
  meta: {
    page: 1,
    size: 25,
    totalItems: items.length,
    totalPages: 1,
    hasNext: false,
    hasPrevious: false,
  },
});

export function overview(isActive = true) {
  return {
    ...TENANT,
    status: isActive ? "active" : "suspended",
    isActive,
    timezone: "UTC",
    defaultCurrency: "USD",
    users: 3,
    activeUsers: 2,
    storesByStatus: { connected: 2 },
    products: { drafts: 4, products: 9 },
    ordersByStatus: { paid: 5 },
    listingsByStatus: { pending: 0, synced: 8, error: 1, removed: 0 },
    subscription: {
      plan: "growth",
      status: "active",
      aiAddon: false,
      trialEndsAt: NOW,
      currentPeriodEnd: NOW,
      cancelAtPeriodEnd: false,
      hasStripeCustomer: true,
    },
    health: {
      windowHours: 24,
      failedOrderSyncs: 1,
      failedInventorySyncs: 0,
      listingsInError: 2,
      failedNotificationEmails: 0,
    },
  };
}

export const DASHBOARD = {
  generatedAt: NOW,
  tenantsByStatus: { active: 7, trial: 3 },
  tenantsNewWeek: 2,
  tenantsNewMonth: 5,
  usersActive: 21,
  usersNewWeek: 4,
  storesByStatus: { connected: 9 },
  storesByPlatform: { shopify: 8, ebay: 1 },
  productsTotal: 340,
  listingsByStatus: { synced: 200, error: 3 },
  ordersLastDay: 12,
  ordersLastWeek: 80,
  subscriptionsByPlan: { growth: 4, none: 6 },
  subscriptionsByStatus: { active: 4, none: 6 },
  trialsEndingWeek: 1,
  failedLastDay: { order_syncs: 2, inventory_syncs: 0 },
  stuck: { order_syncs: 1 },
  operatorSessionsOpen: 1,
  securityFailuresLastDay: 0,
  signupsByDay: [{ day: "2026-10-08", count: 2 }],
  ordersByDay: [{ day: "2026-10-08", count: 12 }],
  system: { database: true, redis: true, migrationRevision: "0053" },
};

export type Handler = (
  body: unknown,
) => { status?: number; body?: unknown } | unknown;

export interface LogEntry {
  method: string;
  path: string;
  body: unknown;
  auth: string | null;
}

export async function mockPlatform(
  page: Page,
  {
    role = "super_admin",
    permissions = ALL_PERMISSIONS,
    handlers = {},
    reauthPaths = [],
  }: {
    role?: string;
    permissions?: string[];
    handlers?: Record<string, Handler>;
    reauthPaths?: string[];
  } = {},
) {
  const log: LogEntry[] = [];
  let reauthenticated = false;
  const base: Record<string, Handler> = {
    "POST /auth/login": () => ({
      accessToken: "platform-token",
      expiresAt: NOW,
    }),
    "GET /me": () => ({
      id: "op1",
      email: "ops@example.com",
      role,
      permissions,
      lastLoginAt: NOW,
      session: SESSION,
      reauthValidUntil: null,
    }),
    "POST /auth/reauth": () => {
      reauthenticated = true;
      return { reauthenticatedAt: NOW, validUntil: NOW };
    },
    "POST /auth/logout": () => ({ status: 204 }),
    "GET /dashboard": () => DASHBOARD,
    "GET /tenants": () => page1([TENANT]),
    [`GET /workspaces/${TENANT_ID}`]: () => overview(),
    "GET /audit": () => [],
    [`GET /workspaces/${TENANT_ID}/support-session`]: () => null,
    "GET /auth/sessions": () => [SESSION],
  };
  const table = { ...base, ...handlers };

  await page.route("**/api/v1/platform/**", (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(
      /^.*\/api\/v1\/platform/,
      "",
    );
    const body = request.postDataJSON?.() ?? null;
    log.push({
      method: request.method(),
      path,
      body,
      auth: request.headers()["authorization"] ?? null,
    });
    const key = `${request.method()} ${path}`;
    const handler = table[key] ?? table[path];
    if (reauthPaths.includes(path) && !reauthenticated) {
      return fulfil(route, 403, {
        code: "reauth_required",
        message: "Confirm",
        details: [],
        requestId: "r",
      });
    }
    if (!handler)
      return fulfil(route, 404, { code: "not_mocked", message: key });
    const out = handler(body);
    if (
      out &&
      typeof out === "object" &&
      "status" in out &&
      typeof out.status === "number"
    ) {
      const { status, body: payload } = out as {
        status: number;
        body?: unknown;
      };
      return status === 204
        ? route.fulfill({ status })
        : fulfil(route, status, payload ?? {});
    }
    return fulfil(route, 200, out);
  });
  return log;
}

function fulfil(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

export async function signIn(page: Page) {
  await page.goto("/platform");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("Correct-Horse-Battery9");
  await page.getByLabel("One-time code").fill("123456");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("platform-operator-role")).toBeVisible();
}

export async function confirmReauth(page: Page) {
  const dialog = page.getByTestId("platform-reauth-dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Password").fill("Correct-Horse-Battery9");
  await dialog.getByLabel("New one-time code").fill("654321");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog).toBeHidden();
}

export async function goTo(page: Page, section: string) {
  await page
    .getByRole("navigation", { name: "Console sections" })
    .getByRole("link", { name: section })
    .click();
}
