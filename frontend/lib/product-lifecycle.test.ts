import { describe, expect, it } from "vitest";

import {
  compareDraftToShopifySync,
  deriveProductLifecycle,
  hasUnsentShopifyChanges,
} from "@/lib/product-lifecycle";
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
  lastSyncedAt: "2026-09-12T10:00:00.000Z",
  lastError: null,
  publishedAt: null,
  lastFailedSyncAt: null,
  ...overrides,
});

describe("compareDraftToShopifySync", () => {
  it("returns unsent when draft is newer than last sync", () => {
    expect(
      compareDraftToShopifySync(
        "2026-09-12T11:00:00.000Z",
        "2026-09-12T10:00:00.000Z",
      ),
    ).toBe("unsent");
  });

  it("returns up_to_date when draft matches or predates last sync", () => {
    expect(
      compareDraftToShopifySync(
        "2026-09-12T10:00:00.000Z",
        "2026-09-12T10:00:00.000Z",
      ),
    ).toBe("up_to_date");
  });

  it("returns unknown when either timestamp is missing", () => {
    expect(compareDraftToShopifySync(null, "2026-09-12T10:00:00.000Z")).toBe(
      "unknown",
    );
  });
});

describe("deriveProductLifecycle", () => {
  it("returns draft when no listing exists", () => {
    const view = deriveProductLifecycle({ syncedListing: null });
    expect(view.kind).toBe("draft_not_on_shopify");
    expect(view.badgeLabel).toBe("Draft — not on Shopify");
  });

  it("never claims live when listing exists without visibility proof", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: null }),
      draftUpdatedAt: "2026-09-12T10:00:00.000Z",
    });
    expect(view.kind).toBe("added_to_shopify");
    expect(view.badgeLabel).not.toMatch(/live/i);
  });

  it("uses up to date only when draft and sync timestamps match", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: true,
        lastSyncedAt: "2026-09-12T10:00:00.000Z",
      }),
      draftUpdatedAt: "2026-09-12T10:00:00.000Z",
    });
    expect(view.kind).toBe("up_to_date_on_shopify");
    expect(view.badgeLabel).toBe("Up to date on Shopify");
  });

  it("shows unsaved changes when dirty on a synced listing", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing(),
      draftUpdatedAt: "2026-09-12T11:00:00.000Z",
      dirty: true,
    });
    expect(view.kind).toBe("unsaved_changes");
    expect(view.badgeLabel).toBe("Unsaved changes");
  });

  it("shows saved-not-sent after autosave when draft is newer than last sync", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: "2026-09-12T10:00:00.000Z" }),
      draftUpdatedAt: "2026-09-12T11:00:00.000Z",
      dirty: false,
    });
    expect(view.kind).toBe("changes_not_sent");
    expect(view.badgeLabel).toBe("Changes saved in DropPilot — not sent to Shopify");
    expect(
      hasUnsentShopifyChanges(
        "2026-09-12T11:00:00.000Z",
        "2026-09-12T10:00:00.000Z",
      ),
    ).toBe(true);
    expect(view.hasUnsentShopifyChanges).toBe(true);
  });

  it("does not claim up to date when dirty is false but draft is newer", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: true }),
      draftUpdatedAt: "2026-09-12T12:00:00.000Z",
      dirty: false,
    });
    expect(view.kind).not.toBe("up_to_date_on_shopify");
    expect(view.kind).toBe("changes_not_sent");
  });

  it("uses visible wording only when onlineStorePublished is true and sync unknown", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: true,
        lastSyncedAt: null,
      }),
    });
    expect(view.kind).toBe("visible_on_shop");
  });

  it("returns unavailable when listing load failed", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsError: true,
    });
    expect(view.kind).toBe("unavailable");
  });

  it("returns loading without inventing a positive state", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsLoading: true,
    });
    expect(view.kind).toBe("loading");
  });

  it("uses conservative copy when sync comparison is unknown", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: null }),
      draftUpdatedAt: "2026-09-12T11:00:00.000Z",
      dirty: false,
    });
    expect(view.supportingCopy).toMatch(/may contain changes/i);
  });

  it("shows visibility setup when synced but onlineStorePublished is false", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: false,
        lastSyncedAt: "2026-09-12T10:00:00.000Z",
      }),
      draftUpdatedAt: "2026-09-12T10:00:00.000Z",
    });
    expect(view.kind).toBe("visibility_setup_needed");
    expect(view.badgeLabel).toBe("Added to Shopify");
  });

  it("shows publishing state while publish is pending", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      publishPending: true,
    });
    expect(view.kind).toBe("publishing");
  });

  it("shows publish failed when publish failed without a synced listing", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      publishFailed: true,
    });
    expect(view.kind).toBe("publish_failed");
  });

  it("overlays publish result only when preferPublishOverlay is true", () => {
    const withoutOverlay = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: null }),
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
      preferPublishOverlay: false,
      draftUpdatedAt: "2026-09-12T10:00:00.000Z",
    });
    expect(withoutOverlay.kind).toBe("added_to_shopify");

    const withOverlay = deriveProductLifecycle({
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
      preferPublishOverlay: true,
    });
    expect(withOverlay.hasSyncedListing).toBe(true);
    expect(withOverlay.onlineStorePublished).toBe(true);
  });
});
