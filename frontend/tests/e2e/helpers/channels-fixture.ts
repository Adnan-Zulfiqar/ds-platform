import type { Page, Route } from "@playwright/test";

import type { Store, StoreStatistics } from "@/services/stores";
import type {
  AliExpressConnection,
  AliExpressStatus,
  EbayConnection,
  EbayStatus,
  RoleName,
  ShopifyConnection,
  ShopifyStatus,
  ShopifyWebhookReconcileResult,
} from "@/types/api";

import { mockAuthResponse } from "./editor-fixture";

/**
 * Backend-less channels (UX-L2D-06).
 *
 * Answers exactly the endpoints Integrations and Stores read and write with
 * the current API shapes, from a mutable `world` so a test can start in any
 * real provider state — configured or not, connected, pending, expired,
 * errored, webhook-degraded — and watch the page move through connect,
 * disconnect and webhook retry. OAuth consent pages are stubbed, never
 * reached. Every request is logged so tests can assert what the page asked
 * for and what it did not.
 */

export const SHOP_STORE_ID = "11111111-2222-4333-8444-555555555555";

export interface ChannelsWorld {
  role: RoleName;
  shopify: ShopifyStatus;
  aliexpress: AliExpressStatus;
  ebay: EbayStatus;
  stores: Store[];
  /** Endpoint keys answering 500. */
  fail: Set<"shopify" | "aliexpress" | "ebay" | "stores">;
  /** Make `POST /integrations/aliexpress/connect` answer the backend's 422. */
  aliexpressNotConfigured: boolean;
  /** The next webhook reconcile answer. */
  reconcile: ShopifyWebhookReconcileResult | { status: number; message: string };
}

export interface ChannelsLog {
  requests: { method: string; path: string }[];
}

const now = Date.now();
const iso = (offsetMs = 0) => new Date(now + offsetMs).toISOString();

export function shopifyConnection(overrides: Partial<ShopifyConnection> = {}): ShopifyConnection {
  return {
    id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    storeId: SHOP_STORE_ID,
    shopDomain: "demo-shop.myshopify.com",
    status: "connected",
    scopes: "read_products,write_products,read_orders",
    connectedAt: iso(-86_400_000 * 3),
    lastSyncAt: iso(-3_600_000),
    lastError: null,
    webhooksRegisteredAt: iso(-86_400_000 * 2),
    webhookHealth: "healthy",
    ...overrides,
  };
}

export function aliExpressConnection(overrides: Partial<AliExpressConnection> = {}): AliExpressConnection {
  return {
    id: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
    status: "connected",
    appKey: "512345",
    connectedAt: iso(-86_400_000 * 5),
    lastSyncAt: iso(-7_200_000),
    tokenExpiresAt: iso(86_400_000 * 20),
    isTokenExpired: false,
    lastError: null,
    ...overrides,
  };
}

export function ebayConnection(overrides: Partial<EbayConnection> = {}): EbayConnection {
  return {
    id: "cccccccc-dddd-4eee-8fff-000000000000",
    status: "connected",
    environment: "production",
    ebayUsername: "brighthome_uk",
    marketplaceId: "EBAY_GB",
    accountType: "business",
    scopes: ["sell.inventory", "sell.fulfillment"],
    connectedAt: iso(-86_400_000 * 9),
    lastVerifiedAt: iso(-1_800_000),
    accessTokenExpiresAt: iso(3_600_000),
    needsReconnect: false,
    reconnectReason: null,
    lastError: null,
    ...overrides,
  };
}

export function storeRecord(overrides: Partial<Store> = {}): Store {
  return {
    id: SHOP_STORE_ID,
    name: "Demo Shop",
    slug: "demo-shop",
    platform: "shopify",
    status: "connected",
    storefrontUrl: "https://demo-shop.myshopify.com",
    externalStoreId: "demo-shop.myshopify.com",
    currency: "GBP",
    currencyLastSyncedAt: iso(-86_400_000),
    timezone: "Europe/London",
    settings: {},
    inventorySyncEnabled: true,
    pricingSyncEnabled: true,
    orderSyncEnabled: true,
    lastSyncAt: iso(-3_600_000),
    lastActivityAt: iso(-600_000),
    lastError: null,
    healthScore: 100,
    createdAt: iso(-86_400_000 * 3),
    updatedAt: iso(-600_000),
    ...overrides,
  };
}

export function healthyReconcile(): ShopifyWebhookReconcileResult {
  return {
    storeId: SHOP_STORE_ID,
    healthy: true,
    webhookHealth: "healthy",
    topics: [
      { topic: "products/update", status: "already_present", webhookGid: "gid://shopify/WebhookSubscription/1", detail: null },
      { topic: "orders/create", status: "created", webhookGid: "gid://shopify/WebhookSubscription/2", detail: null },
    ],
    warnings: [],
    listedCount: 1,
    createdCount: 1,
    webhooksRegisteredAt: iso(),
  };
}

/** Nothing connected, every provider configured, no store records. */
export function channelsWorld(overrides: Partial<ChannelsWorld> = {}): ChannelsWorld {
  return {
    role: "owner",
    shopify: { configured: true, connections: [] },
    aliexpress: { connected: false, connection: null },
    ebay: { configured: true, connected: false, connection: null },
    stores: [],
    fail: new Set(),
    aliexpressNotConfigured: false,
    reconcile: healthyReconcile(),
    ...overrides,
  };
}

function statistics(stores: Store[]): StoreStatistics {
  const byStatus: Record<string, number> = {};
  for (const s of stores) byStatus[s.status] = (byStatus[s.status] ?? 0) + 1;
  return {
    totalStores: stores.length,
    byStatus,
    connected: byStatus.connected ?? 0,
    withErrors: byStatus.error ?? 0,
    productCount: stores.length * 3,
    lastActivityAt: stores[0]?.lastActivityAt ?? null,
  };
}

export async function mockChannelsApi(page: Page, world: ChannelsWorld): Promise<ChannelsLog> {
  const auth = mockAuthResponse();
  auth.identity.roles = [world.role];
  const log: ChannelsLog = { requests: [] };

  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: "" }),
  );
  // Provider consent pages are never reached: the browser is handed a page
  // of our own so the test can observe that the redirect happened.
  await page.route("**/admin/oauth/authorize*", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>shopify-consent-stub</title>" }),
  );
  await page.route("https://auth.aliexpress.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>aliexpress-consent-stub</title>" }),
  );
  await page.route("https://auth.ebay.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>ebay-consent-stub</title>" }),
  );

  await page.route("**/api/v1/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "");
    const method = request.method();
    log.requests.push({ method, path });
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    const fail = () =>
      json({ code: "internal_error", message: "Simulated failure", details: [], requestId: "req-channels" }, 500);
    const paged = <T,>(items: T[]) => ({
      items,
      meta: { page: 1, size: 50, totalItems: items.length, totalPages: 1, hasNext: false, hasPrevious: false },
    });

    if (path === "/auth/refresh") return json(auth);
    if (path === "/auth/me") return json(auth.identity);
    if (path === "/auth/logout") return route.fulfill({ status: 204, body: "" });
    if (path === "/auth/google/nonce") return json({ nonce: "n", expiresInSeconds: 300 });
    if (path === "/products/workspace-counts") return json({ drafts: 1, products: 0 });
    if (path === "/notifications/unread-count") return json({ unread: 0 });
    if (path === "/notifications") return json(paged([]));

    if (path === "/integrations/shopify/status") return world.fail.has("shopify") ? fail() : json(world.shopify);
    if (path === "/integrations/aliexpress/status")
      return world.fail.has("aliexpress") ? fail() : json(world.aliexpress);
    if (path === "/integrations/ebay/status") return world.fail.has("ebay") ? fail() : json(world.ebay);
    if (path === "/integrations/woocommerce/stores") return json([]);

    if (path === "/integrations/shopify/connect" && method === "POST") {
      const body = request.postDataJSON() as { shop?: string };
      return json(
        {
          authorizationUrl: `https://${body.shop}/admin/oauth/authorize?client_id=x&state=s`,
          state: "s",
          expiresInSeconds: 600,
        },
        201,
      );
    }
    const disconnectShopify = path.match(/^\/integrations\/shopify\/stores\/([^/]+)$/);
    if (disconnectShopify && method === "DELETE") {
      world.shopify = {
        ...world.shopify,
        connections: world.shopify.connections.filter((c) => c.storeId !== disconnectShopify[1]),
      };
      world.stores = world.stores.map((s) =>
        s.id === disconnectShopify[1] ? { ...s, status: "disconnected", lastError: "Disconnected from DropPilot." } : s,
      );
      return json({ message: "Shopify store disconnected and credentials deleted." });
    }
    if (/^\/integrations\/shopify\/stores\/[^/]+\/webhooks\/reconcile$/.test(path) && method === "POST") {
      const answer = world.reconcile;
      if ("status" in answer) {
        return json({ code: "reconcile_busy", message: answer.message, details: [], requestId: "req-channels" }, answer.status);
      }
      world.shopify = {
        ...world.shopify,
        connections: world.shopify.connections.map((c) =>
          c.storeId === answer.storeId
            ? { ...c, webhookHealth: answer.webhookHealth, webhooksRegisteredAt: answer.webhooksRegisteredAt }
            : c,
        ),
      };
      return json(answer);
    }

    if (path === "/integrations/aliexpress/connect" && method === "POST") {
      if (world.aliexpressNotConfigured) {
        return json(
          {
            code: "validation_error",
            message: "AliExpress is not configured on this server. Set ALIEXPRESS_APP_KEY and ALIEXPRESS_APP_SECRET.",
            details: [],
            requestId: "req-channels",
          },
          422,
        );
      }
      world.aliexpress = {
        connected: false,
        connection: aliExpressConnection({ status: "pending", isTokenExpired: true, tokenExpiresAt: null, connectedAt: null }),
      };
      return json(
        { authorizationUrl: "https://auth.aliexpress.com/oauth/authorize?state=s", state: "s", expiresInSeconds: 600 },
        201,
      );
    }
    if (path === "/integrations/aliexpress/disconnect" && method === "DELETE") {
      world.aliexpress = { connected: false, connection: null };
      return json({ message: "AliExpress has been disconnected and the stored credentials deleted." });
    }

    if (path === "/integrations/ebay/connect" && method === "POST") {
      return json(
        { authorizationUrl: "https://auth.ebay.com/oauth2/authorize?state=s", state: "s", expiresInSeconds: 600 },
        201,
      );
    }
    if (path === "/integrations/ebay/disconnect" && method === "DELETE") {
      world.ebay = { ...world.ebay, connected: false, connection: null };
      return json({ message: "eBay has been disconnected." });
    }

    if (path === "/stores" && method === "GET") return world.fail.has("stores") ? fail() : json(paged(world.stores));
    if (path === "/stores/statistics") return world.fail.has("stores") ? fail() : json(statistics(world.stores));
    if (path === "/stores" && method === "POST") {
      // The manual create endpoint still exists on the backend; the UI must
      // never call it. Answer, but the log will show if it was hit.
      return json({ code: "unexpected", message: "manual store creation reached" }, 500);
    }

    return json({ code: "not_mocked", message: `${method} ${path}` }, 404);
  });

  return log;
}
