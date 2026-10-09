import type { Page, Route } from "@playwright/test";

import type {
  AnalyticsDashboard,
} from "@/services/dashboard";
import type { AppNotification } from "@/services/notifications";
import type {
  AliExpressStatus,
  EbayStatus,
  OrderStatistics,
  Page as ApiPage,
  Product,
  ProductDetail,
  ProductImportRecord,
  ShopifyStatus,
} from "@/types/api";

import { buildSyntheticProduct, mockAuthResponse } from "./editor-fixture";

/**
 * A backend-less shell.
 *
 * Every API call the application shell and its landing pages make is answered
 * here, so shell behaviour — gutters, navigation, breadcrumb, drawer — can be
 * asserted without a database, a broker, or a signed-in account. Pages whose
 * data is not mocked (orders, inventory, …) receive a JSON 404 and render
 * their own error state; the *route* still resolves, which is what the shell
 * suite checks. Nothing here reaches a network host.
 */

export const SHELL_DRAFT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-000000000001";

function paged<T>(items: T[]): ApiPage<T> {
  return {
    items,
    meta: {
      page: 1,
      size: 25,
      totalItems: items.length,
      totalPages: 1,
      hasNext: false,
      hasPrevious: false,
    },
  };
}

function listRow(overrides: Partial<ProductDetail>): Product {
  // A list row is the detail minus its nested collections; the table never
  // reads those, so the extra keys are harmless and the shape stays honest.
  return buildSyntheticProduct(overrides);
}

const now = Date.now();
const iso = (offsetMs = 0) => new Date(now + offsetMs).toISOString();

const drafts: Product[] = [
  listRow({ id: SHELL_DRAFT_ID, title: "Wireless Desk Lamp with USB Charging" }),
  listRow({ id: "aaaaaaaa-bbbb-4ccc-8ddd-000000000002", title: "Foldable Laptop Stand — Aluminium" }),
  listRow({ id: "aaaaaaaa-bbbb-4ccc-8ddd-000000000003", title: "Magnetic Phone Mount for Car Vent" }),
];
const products: Product[] = [
  listRow({ id: "aaaaaaaa-bbbb-4ccc-8ddd-000000000006", title: "Insulated Water Bottle 750ml", status: "active" }),
];

const analytics: AnalyticsDashboard = {
  revenue: "0",
  orderCount: 0,
  productCount: products.length,
  storeCount: 1,
  connectedStoreCount: 1,
  inventoryUnits: 0,
  syncRuns7d: 0,
  syncFailures7d: 0,
  automationRuns7d: 0,
  automationFailures7d: 0,
  unreadNotifications: 1,
  salesSeries: [],
  ordersSeries: [],
  topProducts: [],
  recentActivity: [],
  periodStart: iso(-30 * 86_400_000),
  periodEnd: iso(),
};

const orderStatistics: OrderStatistics = {
  totalOrders: 0,
  byStatus: {},
  pendingFulfillment: 0,
  processing: 0,
  delivered: 0,
  failedSyncsLast7Days: 0,
  lastSync: null,
  webhookEventsReceived: null,
};

const notifications: AppNotification[] = [
  {
    id: "n1",
    kind: "import_completed",
    title: "Import completed",
    body: "Wireless Desk Lamp with USB Charging is ready to review.",
    href: `/drafts/${SHELL_DRAFT_ID}`,
    isRead: false,
    createdAt: iso(-1_800_000),
    readAt: null,
  },
];

const shopifyStatus: ShopifyStatus = {
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
const aliExpressStatus: AliExpressStatus = { connected: false, connection: null };
const ebayStatus: EbayStatus = { configured: false, connected: false, connection: null };

const imports: ProductImportRecord[] = [
  {
    id: "i1",
    source: "aliexpress",
    externalId: "1005000000000001",
    status: "succeeded",
    productId: SHELL_DRAFT_ID,
    errorCode: null,
    errorMessage: null,
    startedAt: iso(-3_700_000),
    finishedAt: iso(-3_600_000),
    createdAt: iso(-3_700_000),
  },
];

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/** Install the mocked backend on a page. Call before the first navigation. */
export async function mockShellApi(page: Page): Promise<void> {
  const auth = mockAuthResponse();

  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: "" }),
  );

  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = request.method();

    if (path === "/auth/refresh") return json(route, auth);
    if (path === "/auth/me") return json(route, auth.identity);
    if (path === "/auth/logout") return route.fulfill({ status: 204, body: "" });
    if (path === "/products/workspace-counts")
      return json(route, { drafts: drafts.length, products: products.length });
    if (path === "/notifications/unread-count")
      return json(route, { unread: notifications.filter((n) => !n.isRead).length });
    if (path === "/system/status") return json(route, { maintenance: false, maintenanceMessage: null, announcements: [] });
    if (path === "/notifications") return json(route, paged(notifications));
    if (path === "/analytics/dashboard") return json(route, analytics);
    if (path === "/orders/statistics") return json(route, orderStatistics);
    if (path === "/drafts") return json(route, paged(drafts));
    if (path === "/products") return json(route, paged(products));
    if (path === "/products/imports") return json(route, paged(imports));
    if (path === "/integrations/shopify/status") return json(route, shopifyStatus);
    if (path === "/integrations/aliexpress/status") return json(route, aliExpressStatus);
    if (path === "/integrations/ebay/status") return json(route, ebayStatus);
    if (path === "/integrations/woocommerce/stores") return json(route, []);
    if (path === "/stores") return json(route, paged([]));
    if (path === "/stores/statistics")
      return json(route, {
        totalStores: 0,
        byStatus: {},
        connected: 0,
        withErrors: 0,
        productCount: 0,
        lastActivityAt: null,
      });
    if (path.startsWith("/drafts/") && method === "GET") {
      if (path.endsWith("/listings")) return json(route, []);
      if (path.endsWith("/seo-score"))
        return json(route, {
          score: 72,
          status: "good",
          sections: {},
          warnings: [],
          explanations: [],
          metaKeywordsExported: false,
          note: "",
        });
      return json(route, buildSyntheticProduct({ id: SHELL_DRAFT_ID }));
    }
    return json(route, { code: "not_mocked", message: `${method} ${path}` }, 404);
  });
}
