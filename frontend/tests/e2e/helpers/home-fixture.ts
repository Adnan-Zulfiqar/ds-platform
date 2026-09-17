import type { Page, Route } from "@playwright/test";

import type { AppNotification } from "@/services/notifications";
import type {
  AliExpressStatus,
  EbayStatus,
  OrderStatistics,
  Page as ApiPage,
  Product,
  ProductImportRecord,
  ProductWorkspaceCounts,
  ShopifyStatus,
} from "@/types/api";

import { buildSyntheticProduct, mockAuthResponse } from "./editor-fixture";

/**
 * Backend-less Home (UX-L2D-03).
 *
 * `mockHomeApi` answers every request the Home page and the shell make, from
 * a scenario the test builds with `homeScenario`. Endpoints listed in
 * `fail` answer 500 so partial-failure behaviour can be exercised; a test can
 * flip a scenario's `fail` set between requests to prove a block's Retry.
 * Nothing here reaches a network host.
 */

export interface HomeScenario {
  counts: ProductWorkspaceCounts;
  shopify: ShopifyStatus;
  aliexpress: AliExpressStatus;
  ebay: EbayStatus;
  drafts: Product[];
  imports: ProductImportRecord[];
  orderStatistics: OrderStatistics;
  notifications: AppNotification[];
  /** Endpoint keys that answer 500. */
  fail: Set<HomeEndpoint>;
  /** Milliseconds to hold each listed endpoint before answering. */
  delayMs: Partial<Record<HomeEndpoint, number>>;
}

export type HomeEndpoint =
  | "counts"
  | "shopify"
  | "aliexpress"
  | "ebay"
  | "drafts"
  | "imports"
  | "orderStatistics"
  | "notifications";

const now = Date.now();
export const iso = (offsetMs = 0) => new Date(now + offsetMs).toISOString();

export const HOME_DRAFT_IDS = [
  "aaaaaaaa-bbbb-4ccc-8ddd-000000000101",
  "aaaaaaaa-bbbb-4ccc-8ddd-000000000102",
  "aaaaaaaa-bbbb-4ccc-8ddd-000000000103",
] as const;

export function draft(id: string, overrides: Partial<Product> = {}): Product {
  return buildSyntheticProduct({ id, ...overrides });
}

export const CONNECTED_SHOPIFY: ShopifyStatus = {
  configured: true,
  connections: [
    {
      id: "c1",
      storeId: "s1",
      shopDomain: "demo-shop.myshopify.com",
      status: "connected",
      scopes: "write_products,read_orders",
      connectedAt: iso(-86_400_000 * 10),
      lastSyncAt: iso(-3_600_000),
      lastError: null,
      webhooksRegisteredAt: iso(-86_400_000 * 10),
      webhookHealth: "healthy",
    },
  ],
};

export const NO_SHOPIFY: ShopifyStatus = { configured: true, connections: [] };

export const CONNECTED_ALIEXPRESS: AliExpressStatus = {
  connected: true,
  connection: {
    id: "a1",
    status: "connected",
    appKey: "12345",
    connectedAt: iso(-86_400_000 * 40),
    lastSyncAt: iso(-86_400_000),
    tokenExpiresAt: iso(86_400_000 * 20),
    isTokenExpired: false,
    lastError: null,
  },
};

export const EXPIRED_ALIEXPRESS: AliExpressStatus = {
  connected: false,
  connection: {
    id: "a1",
    status: "expired",
    appKey: "12345",
    connectedAt: iso(-86_400_000 * 40),
    lastSyncAt: iso(-86_400_000 * 3),
    tokenExpiresAt: iso(-86_400_000),
    isTokenExpired: true,
    lastError: "Token expired",
  },
};

export const NO_ALIEXPRESS: AliExpressStatus = { connected: false, connection: null };
export const UNCONFIGURED_EBAY: EbayStatus = { configured: false, connected: false, connection: null };

export const ZERO_ORDERS: OrderStatistics = {
  totalOrders: 0,
  byStatus: {},
  pendingFulfillment: 0,
  processing: 0,
  delivered: 0,
  failedSyncsLast7Days: 0,
  lastSync: null,
  webhookEventsReceived: null,
};

/** A workspace that has been used: connected channels, drafts, activity. */
export function populatedScenario(): HomeScenario {
  return {
    counts: { drafts: 7, products: 2 },
    shopify: CONNECTED_SHOPIFY,
    aliexpress: CONNECTED_ALIEXPRESS,
    ebay: UNCONFIGURED_EBAY,
    drafts: [
      draft(HOME_DRAFT_IDS[0], {
        title: "Wireless Desk Lamp with USB Charging",
        updatedAt: iso(-15 * 60_000),
        aiStatus: "optimized",
      }),
      draft(HOME_DRAFT_IDS[1], {
        title: "Foldable Laptop Stand — Aluminium",
        updatedAt: iso(-3 * 3_600_000),
        aiStatus: "not_optimized",
      }),
      draft(HOME_DRAFT_IDS[2], {
        title: "Magnetic Phone Mount for Car Vent",
        updatedAt: iso(-2 * 86_400_000),
        aiStatus: "not_optimized",
      }),
    ],
    imports: [
      {
        id: "i1",
        source: "aliexpress",
        externalId: "1005000000000101",
        status: "succeeded",
        productId: HOME_DRAFT_IDS[0],
        errorCode: null,
        errorMessage: null,
        startedAt: iso(-3_700_000),
        finishedAt: iso(-3_600_000),
        createdAt: iso(-3_700_000),
      },
    ],
    orderStatistics: { ...ZERO_ORDERS, totalOrders: 12, pendingFulfillment: 3, delivered: 9 },
    notifications: [
      {
        id: "n1",
        kind: "import_completed",
        title: "Import completed",
        body: "Wireless Desk Lamp with USB Charging is ready to review.",
        href: `/drafts/${HOME_DRAFT_IDS[0]}`,
        isRead: false,
        createdAt: iso(-3_600_000),
        readAt: null,
      },
      {
        id: "n2",
        kind: "automation_completed",
        title: "Pricing rule applied",
        body: "Standard markup applied to 12 products.",
        href: "/pricing",
        isRead: true,
        createdAt: iso(-20_000_000),
        readAt: iso(-19_000_000),
      },
    ],
    fail: new Set(),
    delayMs: {},
  };
}

/** A brand-new workspace: nothing connected, nothing imported. */
export function emptyScenario(): HomeScenario {
  return {
    counts: { drafts: 0, products: 0 },
    shopify: NO_SHOPIFY,
    aliexpress: NO_ALIEXPRESS,
    ebay: UNCONFIGURED_EBAY,
    drafts: [],
    imports: [],
    orderStatistics: ZERO_ORDERS,
    notifications: [],
    fail: new Set(),
    delayMs: {},
  };
}

/** Several real things wrong at once. */
export function attentionScenario(): HomeScenario {
  const base = populatedScenario();
  return {
    ...base,
    aliexpress: EXPIRED_ALIEXPRESS,
    shopify: {
      configured: true,
      connections: [
        ...CONNECTED_SHOPIFY.connections,
        {
          id: "c2",
          storeId: "s2",
          shopDomain: "second-shop.myshopify.com",
          status: "connected",
          scopes: "write_products",
          connectedAt: iso(-86_400_000 * 2),
          lastSyncAt: null,
          lastError: null,
          webhooksRegisteredAt: null,
          webhookHealth: "degraded",
        },
      ],
    },
    drafts: [
      draft(HOME_DRAFT_IDS[0], { title: "Bluetooth Sleep Headband", aiStatus: "failed", updatedAt: iso(-600_000) }),
      ...base.drafts.slice(1),
    ],
    imports: [
      {
        id: "i9",
        source: "aliexpress",
        externalId: "1005000000000999",
        status: "failed",
        productId: null,
        errorCode: "supplier_unavailable",
        errorMessage: "Supplier listing is no longer available.",
        startedAt: iso(-8_000_000),
        finishedAt: iso(-7_900_000),
        createdAt: iso(-8_000_000),
      },
      ...base.imports,
    ],
    orderStatistics: { ...base.orderStatistics, failedSyncsLast7Days: 2 },
    notifications: [
      {
        id: "n9",
        kind: "webhook_failure",
        title: "Shopify webhook rejected",
        body: "demo-shop.myshopify.com returned 401 for orders/create.",
        href: "/settings/integrations",
        isRead: false,
        createdAt: iso(-1_200_000),
        readAt: null,
      },
      ...base.notifications,
    ],
  };
}

function paged<T>(items: T[]): ApiPage<T> {
  return {
    items,
    meta: {
      page: 1,
      size: items.length || 25,
      totalItems: items.length,
      totalPages: 1,
      hasNext: false,
      hasPrevious: false,
    },
  };
}

async function answer(
  route: Route,
  scenario: HomeScenario,
  key: HomeEndpoint,
  body: () => unknown,
): Promise<void> {
  const delay = scenario.delayMs[key];
  if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
  if (scenario.fail.has(key)) {
    return route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ code: "internal_error", message: "Simulated failure", requestId: "req-home" }),
    });
  }
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body()),
  });
}

/** Query strings the page sent, by endpoint, so tests can assert wire params. */
export interface RequestLog {
  drafts: string[];
}

export async function mockHomeApi(page: Page, scenario: HomeScenario): Promise<RequestLog> {
  const auth = mockAuthResponse();
  const log: RequestLog = { drafts: [] };

  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: "" }),
  );

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/auth/refresh") return json(auth);
    if (path === "/auth/me") return json(auth.identity);
    if (path === "/auth/logout") return route.fulfill({ status: 204, body: "" });
    if (path === "/auth/google/nonce") return json({ nonce: "n", expiresInSeconds: 300 });

    if (path === "/products/workspace-counts") return answer(route, scenario, "counts", () => scenario.counts);
    if (path === "/integrations/shopify/status") return answer(route, scenario, "shopify", () => scenario.shopify);
    if (path === "/integrations/aliexpress/status")
      return answer(route, scenario, "aliexpress", () => scenario.aliexpress);
    if (path === "/integrations/ebay/status") return answer(route, scenario, "ebay", () => scenario.ebay);
    if (path === "/drafts") {
      log.drafts.push(url.search);
      return answer(route, scenario, "drafts", () => paged(scenario.drafts));
    }
    if (path === "/products/imports") return answer(route, scenario, "imports", () => paged(scenario.imports));
    if (path === "/orders/statistics")
      return answer(route, scenario, "orderStatistics", () => scenario.orderStatistics);
    if (path === "/notifications/unread-count")
      return json({ unread: scenario.notifications.filter((n) => !n.isRead).length });
    if (path === "/notifications")
      return answer(route, scenario, "notifications", () => paged(scenario.notifications));
    if (path === "/products/import/check") return json({ matches: [] });
    if (path === "/stores") return json(paged([]));

    return json({ code: "not_mocked", message: `${request.method()} ${path}` }, 404);
  });

  return log;
}
