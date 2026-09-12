import { describe, expect, it } from "vitest";

import { deriveProductLifecycle } from "@/lib/product-lifecycle";
import type { StoreListing } from "@/types/api";

const syncedListing = (
  overrides: Partial<StoreListing> = {},
): StoreListing => ({
  id: "11111111-1111-4111-8111-111111111111",
  storeId: "22222222-2222-4222-8222-222222222222",
  productId: "33333333-3333-4333-8333-333333333333",
  externalProductId: "1001",
  externalHandle: "lamp",
  externalGraphqlId: null,
  shopDomain: "demo.myshopify.com",
  storefrontUrl: null,
  adminUrl: "https://demo.myshopify.com/admin/products/1001",
  onlineStorePublished: null,
  status: "synced",
  lastSyncedAt: "2026-09-12T00:00:00.000Z",
  lastError: null,
  publishedAt: null,
  lastFailedSyncAt: null,
  ...overrides,
});

describe("deriveProductLifecycle", () => {
  it("returns draft when no listing exists", () => {
    const view = deriveProductLifecycle({ syncedListing: null });
    expect(view.kind).toBe("draft_not_on_shopify");
    expect(view.badgeLabel).toBe("Draft — not on Shopify");
    expect(view.hasSyncedListing).toBe(false);
  });

  it("never claims live when listing exists without visibility proof", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: null }),
    });
    expect(view.kind).toBe("added_to_shopify");
    expect(view.badgeLabel).not.toMatch(/live/i);
    expect(view.badgeLabel).toBe("Added to Shopify");
  });

  it("uses visible wording only when onlineStorePublished is true", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: true,
        storefrontUrl: "https://demo.myshopify.com/products/lamp",
      }),
    });
    expect(view.kind).toBe("visible_on_shop");
    expect(view.badgeLabel).toMatch(/Visible on your shop/i);
  });

  it("handles visibility setup needed when onlineStorePublished is false", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: false }),
    });
    expect(view.kind).toBe("visibility_setup_needed");
    expect(view.supportingCopy).toMatch(/may not be visible/i);
  });

  it("returns unavailable when listing load failed", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsError: true,
    });
    expect(view.kind).toBe("unavailable");
    expect(view.badgeLabel).toBe("Shopify status unavailable");
  });

  it("returns loading without inventing a positive state", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsLoading: true,
    });
    expect(view.kind).toBe("loading");
    expect(view.hasSyncedListing).toBe(false);
  });

  it("returns changes not sent when dirty after sync", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing(),
      dirty: true,
    });
    expect(view.kind).toBe("changes_not_sent");
    expect(view.badgeLabel).toBe("Changes not sent to Shopify");
  });

  it("prefers publish result fields until listing refetch", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      publishResult: {
        message: "Published.",
        listingId: "44444444-4444-4444-8444-444444444444",
        externalProductId: "1001",
        externalHandle: "lamp",
        externalGraphqlId: null,
        shopDomain: "demo.myshopify.com",
        storefrontUrl: "https://demo.myshopify.com/products/lamp",
        adminUrl: "https://demo.myshopify.com/admin/products/1001",
        onlineStorePublished: true,
        updated: true,
      },
    });
    expect(view.kind).toBe("visible_on_shop");
    expect(view.hasSyncedListing).toBe(true);
  });
});
