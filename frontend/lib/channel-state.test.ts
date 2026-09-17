import { describe, expect, it } from "vitest";

import {
  CHANNEL_LABEL,
  deriveAliExpressChannel,
  deriveEbayChannel,
  deriveShopifyChannel,
  deriveShopifyConnection,
  deriveStoreRecordState,
  PLATFORM_LABEL,
} from "@/lib/channel-state";
import type { AliExpressConnection, EbayConnection, ShopifyConnection } from "@/types/api";

/**
 * Scenario tests for the channel vocabulary (UX-L2D-06). Each case is a
 * payload the API can actually return; nothing here invents a field.
 */

function shopifyConnection(overrides: Partial<ShopifyConnection> = {}): ShopifyConnection {
  return {
    id: "c1",
    storeId: "s1",
    shopDomain: "demo-shop.myshopify.com",
    status: "connected",
    scopes: "read_products",
    connectedAt: "2026-08-18T08:15:00Z",
    lastSyncAt: null,
    lastError: null,
    webhooksRegisteredAt: "2026-08-19T11:40:05Z",
    webhookHealth: "healthy",
    ...overrides,
  };
}

function aliExpressConnection(overrides: Partial<AliExpressConnection> = {}): AliExpressConnection {
  return {
    id: "a1",
    status: "connected",
    appKey: "public-key",
    connectedAt: "2026-08-18T08:15:00Z",
    lastSyncAt: null,
    tokenExpiresAt: "2099-01-01T00:00:00Z",
    isTokenExpired: false,
    lastError: null,
    ...overrides,
  };
}

function ebayConnection(overrides: Partial<EbayConnection> = {}): EbayConnection {
  return {
    id: "e1",
    status: "connected",
    environment: "production",
    ebayUsername: "seller-demo",
    marketplaceId: "EBAY_GB",
    accountType: "business",
    scopes: ["sell.inventory"],
    connectedAt: "2026-08-18T08:15:00Z",
    lastVerifiedAt: "2026-08-19T11:40:05Z",
    accessTokenExpiresAt: "2099-01-01T00:00:00Z",
    needsReconnect: false,
    reconnectReason: null,
    lastError: null,
    ...overrides,
  };
}

const settled = <T,>(data: T) => ({ data, isPending: false, isError: false });
const loading = { data: undefined, isPending: true, isError: false };
const failed = { data: undefined, isPending: false, isError: true };

describe("deriveShopifyConnection", () => {
  it("a connected store with confirmed webhooks is connected", () => {
    const s = deriveShopifyConnection(shopifyConnection());
    expect(s.kind).toBe("connected");
    expect(s.webhookLabel).toBe("Webhooks last confirmed");
    expect(s.actions).toEqual(["disconnect"]);
  });

  it("a connected store with degraded webhooks needs attention and offers the retry", () => {
    const s = deriveShopifyConnection(
      shopifyConnection({ webhooksRegisteredAt: null, webhookHealth: "degraded" }),
    );
    expect(s.kind).toBe("needs-attention");
    expect(s.webhookLabel).toBe("Webhooks incomplete");
    expect(s.actions).toEqual(["retry-webhooks", "disconnect"]);
    expect(s.detail).toBe("Webhook setup for this store did not complete.");
    // The consequence belongs to the card's warning alert alone; the row
    // must not repeat it (one status, one consequence).
    expect(s.detail).not.toMatch(/updates may be missed/i);
  });

  it("an errored store never shows the raw provider text", () => {
    const s = deriveShopifyConnection(
      shopifyConnection({ status: "error", lastError: 'HTTP 401 {"errors":"[API] Invalid API key"}' }),
    );
    expect(s.kind).toBe("needs-attention");
    expect(s.actions).toEqual(["reconnect", "disconnect"]);
    expect(`${s.label} ${s.detail}`).not.toMatch(/401|Invalid API key|errors/);
  });

  it("expired is reconnect required; pending is awaiting authorization", () => {
    expect(deriveShopifyConnection(shopifyConnection({ status: "expired" })).kind).toBe("reconnect-required");
    const pending = deriveShopifyConnection(shopifyConnection({ status: "pending" }));
    expect(pending.kind).toBe("pending");
    expect(pending.label).toBe("Awaiting authorization");
  });
});

describe("deriveShopifyChannel", () => {
  it("loading is checking; a failed request is unavailable, never not connected", () => {
    expect(deriveShopifyChannel(loading).kind).toBe("checking");
    const s = deriveShopifyChannel(failed);
    expect(s.kind).toBe("unavailable");
    expect(s.canConnect).toBe(false);
  });

  it("no app credentials on the server is setup unavailable with no connect", () => {
    const s = deriveShopifyChannel(settled({ configured: false, connections: [] }));
    expect(s.kind).toBe("setup-unavailable");
    expect(s.label).toBe("Setup unavailable");
    expect(s.canConnect).toBe(false);
    expect(s.detail).toMatch(/operator/i);
    expect(s.detail).not.toMatch(/SHOPIFY_|API_KEY|secret/);
  });

  it("configured with no rows is not connected and can connect", () => {
    const s = deriveShopifyChannel(settled({ configured: true, connections: [] }));
    expect(s.kind).toBe("not-connected");
    expect(s.canConnect).toBe(true);
  });

  it("summarises counts: N connected", () => {
    const s = deriveShopifyChannel(
      settled({ configured: true, connections: [shopifyConnection(), shopifyConnection({ id: "c2", storeId: "s2" })] }),
    );
    expect(s.kind).toBe("connected");
    expect(s.label).toBe("2 connected");
  });

  it("one degraded store makes the summary 'N needs webhook setup'", () => {
    const s = deriveShopifyChannel(
      settled({
        configured: true,
        connections: [shopifyConnection(), shopifyConnection({ id: "c2", storeId: "s2", webhooksRegisteredAt: null, webhookHealth: "degraded" })],
      }),
    );
    expect(s.kind).toBe("needs-attention");
    expect(s.label).toBe("1 needs webhook setup");
  });

  it("only an expired store is reconnect required at the summary level", () => {
    const s = deriveShopifyChannel(settled({ configured: true, connections: [shopifyConnection({ status: "expired" })] }));
    expect(s.kind).toBe("reconnect-required");
  });

  it("a connected store beside an errored one is needs attention, not connected", () => {
    const s = deriveShopifyChannel(
      settled({ configured: true, connections: [shopifyConnection(), shopifyConnection({ id: "c2", storeId: "s2", status: "error" })] }),
    );
    expect(s.kind).toBe("needs-attention");
    expect(s.label).toBe("Needs attention");
  });
});

describe("deriveAliExpressChannel", () => {
  it("null connection is not connected with a connect action", () => {
    const s = deriveAliExpressChannel(settled({ connected: false, connection: null }));
    expect(s.kind).toBe("not-connected");
    expect(s.actions).toEqual(["connect"]);
  });

  it("the server's computed `connected` is the authority", () => {
    const s = deriveAliExpressChannel(settled({ connected: true, connection: aliExpressConnection() }));
    expect(s.kind).toBe("connected");
    expect(s.actions).toEqual(["reconnect", "disconnect"]);
  });

  it("a connected row whose token expired is reconnect required, not connected", () => {
    const s = deriveAliExpressChannel(
      settled({ connected: false, connection: aliExpressConnection({ isTokenExpired: true }) }),
    );
    expect(s.kind).toBe("reconnect-required");
  });

  it("expired status is reconnect required and keeps the curated message", () => {
    const s = deriveAliExpressChannel(
      settled({
        connected: false,
        connection: aliExpressConnection({
          status: "expired",
          isTokenExpired: true,
          lastError: "AliExpress rejected the credentials for this connection.",
        }),
      }),
    );
    expect(s.kind).toBe("reconnect-required");
    expect(s.message).toBe("AliExpress rejected the credentials for this connection.");
  });

  it("pending offers continue and disconnect", () => {
    const s = deriveAliExpressChannel(
      settled({ connected: false, connection: aliExpressConnection({ status: "pending", isTokenExpired: true }) }),
    );
    expect(s.kind).toBe("pending");
    expect(s.label).toBe("Awaiting authorization");
    expect(s.actions).toEqual(["continue", "disconnect"]);
  });

  it("error is needs attention", () => {
    const s = deriveAliExpressChannel(
      settled({ connected: false, connection: aliExpressConnection({ status: "error", lastError: "AliExpress did not respond in time." }) }),
    );
    expect(s.kind).toBe("needs-attention");
    expect(s.message).toMatch(/did not respond/);
  });

  it("setup unavailable overrides everything and offers no action", () => {
    const s = deriveAliExpressChannel(settled({ connected: false, connection: null }), { setupUnavailable: true });
    expect(s.kind).toBe("setup-unavailable");
    expect(s.actions).toEqual([]);
    expect(s.detail).not.toMatch(/ALIEXPRESS_|APP_KEY|APP_SECRET/);
  });

  it("loading and failure are their own states", () => {
    expect(deriveAliExpressChannel(loading).kind).toBe("checking");
    expect(deriveAliExpressChannel(failed).kind).toBe("unavailable");
  });
});

describe("deriveEbayChannel", () => {
  it("not configured is setup unavailable with no actions", () => {
    const s = deriveEbayChannel(settled({ configured: false, connected: false, connection: null }));
    expect(s.kind).toBe("setup-unavailable");
    expect(s.actions).toEqual([]);
    // The word the existing eBay spec probes for.
    expect(s.label.toLowerCase()).toContain("unavailable");
  });

  it("connected shows reconnect and disconnect", () => {
    const s = deriveEbayChannel(settled({ configured: true, connected: true, connection: ebayConnection() }));
    expect(s.kind).toBe("connected");
    expect(s.actions).toEqual(["reconnect", "disconnect"]);
  });

  it("needsReconnect maps the machine code to a merchant reason", () => {
    const s = deriveEbayChannel(
      settled({
        configured: true,
        connected: false,
        connection: ebayConnection({ status: "reconnect_required", needsReconnect: true, reconnectReason: "refresh_token_revoked" }),
      }),
    );
    expect(s.kind).toBe("reconnect-required");
    expect(s.reason).toMatch(/password or username change/i);
    expect(s.reason).not.toMatch(/refresh_token_revoked/);
  });

  it("an unknown reason code gets the generic sentence, not the code", () => {
    const s = deriveEbayChannel(
      settled({
        configured: true,
        connected: false,
        connection: ebayConnection({ status: "reconnect_required", needsReconnect: true, reconnectReason: "something_new" }),
      }),
    );
    expect(s.reason).not.toMatch(/something_new/);
  });

  it("pending and error", () => {
    expect(
      deriveEbayChannel(settled({ configured: true, connected: false, connection: ebayConnection({ status: "pending" }) })).kind,
    ).toBe("pending");
    expect(
      deriveEbayChannel(settled({ configured: true, connected: false, connection: ebayConnection({ status: "error" }) })).kind,
    ).toBe("needs-attention");
  });
});

describe("deriveStoreRecordState", () => {
  it("maps every store status to a merchant word", () => {
    expect(deriveStoreRecordState({ status: "connected", lastError: null }).label).toBe("Connected");
    expect(deriveStoreRecordState({ status: "syncing", lastError: null }).label).toBe("Syncing");
    expect(deriveStoreRecordState({ status: "error", lastError: "boom" }).label).toBe("Needs attention");
    expect(deriveStoreRecordState({ status: "disconnected", lastError: "Disconnected from DropPilot." }).label).toBe("Disconnected");
    const pending = deriveStoreRecordState({ status: "pending", lastError: null });
    expect(pending.label).toBe("Setup incomplete");
    expect(pending.detail).toMatch(/never authorized/i);
  });

  it("never echoes a store's raw lastError", () => {
    const s = deriveStoreRecordState({ status: "error", lastError: 'HTTP 500 {"x":1}' });
    expect(`${s.label} ${s.detail}`).not.toContain("HTTP 500");
  });
});

describe("vocabulary", () => {
  it("has a label for every kind and every platform", () => {
    for (const label of Object.values(CHANNEL_LABEL)) expect(label.length).toBeGreaterThan(0);
    expect(PLATFORM_LABEL.tiktok_shop).toBe("TikTok Shop");
    expect(PLATFORM_LABEL.ebay).toBe("eBay");
  });

  it("never uses the words healthy, active or live", () => {
    for (const label of Object.values(CHANNEL_LABEL)) {
      expect(label).not.toMatch(/\b(healthy|active|live)\b/i);
    }
  });
});
