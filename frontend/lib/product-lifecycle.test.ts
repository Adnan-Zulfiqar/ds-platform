import { describe, expect, it } from "vitest";

import {
  compareDraftToShopifySync,
  CONSERVATIVE_SHOPIFY_SYNC_COPY,
  deriveListingsQueryLifecycleFlags,
  deriveProductLifecycle,
  hasUnsentShopifyChanges,
  REFRESH_FAILED_SHOPIFY_STATUS_COPY,
  REFRESHING_SHOPIFY_STATUS_COPY,
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

const syncedAt = "2026-09-12T10:00:00.000Z";

describe("compareDraftToShopifySync", () => {
  it("returns unsent when draft is newer than last sync", () => {
    expect(
      compareDraftToShopifySync("2026-09-12T11:00:00.000Z", syncedAt),
    ).toBe("unsent");
  });

  it("returns not_unsent when draft matches or predates last sync", () => {
    expect(compareDraftToShopifySync(syncedAt, syncedAt)).toBe("not_unsent");
    expect(
      compareDraftToShopifySync("2026-09-12T09:00:00.000Z", syncedAt),
    ).toBe("not_unsent");
  });

  it("returns unknown when lastSyncedAt is missing", () => {
    expect(compareDraftToShopifySync("2026-09-12T10:00:00.000Z", null)).toBe(
      "unknown",
    );
  });

  it("returns unknown when draftUpdatedAt is missing", () => {
    expect(compareDraftToShopifySync(null, syncedAt)).toBe("unknown");
  });

  it("returns unknown for invalid timestamps", () => {
    expect(compareDraftToShopifySync("not-a-date", syncedAt)).toBe("unknown");
    expect(compareDraftToShopifySync(syncedAt, "also-invalid")).toBe("unknown");
  });

  it("treats equal instants across timezone offsets as not_unsent", () => {
    expect(
      compareDraftToShopifySync("2026-09-12T11:00:00+01:00", syncedAt),
    ).toBe("not_unsent");
  });

  it("detects unsent when draft is newer by milliseconds", () => {
    expect(
      compareDraftToShopifySync("2026-09-12T10:00:00.001Z", syncedAt),
    ).toBe("unsent");
  });

  it("returns not_unsent when draft is older than last sync", () => {
    expect(
      compareDraftToShopifySync("2026-09-12T09:59:59.999Z", syncedAt),
    ).toBe("not_unsent");
  });
});

describe("deriveProductLifecycle — sync authority", () => {
  it("never emits Up to date on Shopify from timestamp comparison alone", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: true,
        lastSyncedAt: syncedAt,
      }),
      draftUpdatedAt: syncedAt,
    });
    expect(view.badgeLabel).not.toBe("Up to date on Shopify");
    expect(view.kind).toBe("visible_on_shop");
  });

  it("uses conservative copy when sync timestamps match but visibility unknown", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: syncedAt }),
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("added_to_shopify");
    expect(view.supportingCopy).toBe(CONSERVATIVE_SHOPIFY_SYNC_COPY);
  });

  it("treats inventory-only sync after draft edit as unsent when draft is newer", () => {
    const inventoryPushAt = "2026-09-12T11:30:00.000Z";
    const draftEditAt = "2026-09-12T11:45:00.000Z";
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: inventoryPushAt }),
      draftUpdatedAt: draftEditAt,
      dirty: false,
    });
    expect(view.kind).toBe("changes_not_sent");
    expect(hasUnsentShopifyChanges(draftEditAt, inventoryPushAt)).toBe(true);
  });

  it("does not claim full sync when price-only push advanced lastSyncedAt", () => {
    const pricePushAt = "2026-09-12T12:00:00.000Z";
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: true,
        lastSyncedAt: pricePushAt,
      }),
      draftUpdatedAt: pricePushAt,
      dirty: false,
    });
    expect(view.kind).toBe("visible_on_shop");
    expect(view.badgeLabel).not.toMatch(/up to date/i);
  });

  it("shows unsent after supplier refresh bumps draft updatedAt", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: syncedAt }),
      draftUpdatedAt: "2026-09-12T12:00:00.000Z",
      dirty: false,
    });
    expect(view.kind).toBe("changes_not_sent");
  });

  it("falls back to conservative state when comparison is unknown", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: null }),
      draftUpdatedAt: syncedAt,
      dirty: false,
    });
    expect(view.supportingCopy).toMatch(/may contain changes/i);
  });

  it("does not mark current when publish failed without a synced listing", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ status: "error", lastSyncedAt: null }),
      publishFailed: true,
    });
    expect(view.kind).toBe("publish_failed");
    expect(view.hasSyncedListing).toBe(false);
  });

  it("shows publishing while publish is in flight", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing(),
      publishPending: true,
    });
    expect(view.kind).toBe("publishing");
  });

  it("shows unsaved changes when dirty even if timestamps match", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: syncedAt }),
      draftUpdatedAt: syncedAt,
      dirty: true,
    });
    expect(view.kind).toBe("unsaved_changes");
  });

  it("shows saved-not-sent after autosave when draft is newer than last sync", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: syncedAt }),
      draftUpdatedAt: "2026-09-12T11:00:00.000Z",
      dirty: false,
    });
    expect(view.kind).toBe("changes_not_sent");
    expect(view.hasUnsentShopifyChanges).toBe(true);
  });
});

describe("deriveProductLifecycle — baseline states", () => {
  it("returns draft when no listing exists after confirmed empty response", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsHasConfirmedData: true,
    });
    expect(view.kind).toBe("draft_not_on_shopify");
  });

  it("never claims live when listing exists without visibility proof", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: null }),
      draftUpdatedAt: syncedAt,
    });
    expect(view.badgeLabel).not.toMatch(/live/i);
  });

  it("uses visible wording only when onlineStorePublished is true", () => {
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
      listingsInitialError: true,
    });
    expect(view.kind).toBe("unavailable");
    expect(view.showListingsRetry).toBe(true);
  });

  it("returns loading without inventing a positive state", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsInitialLoading: true,
    });
    expect(view.kind).toBe("loading");
    expect(view.badgeLabel).toBe("Checking Shopify status…");
  });

  it("shows visibility setup when synced but onlineStorePublished is false", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: false,
        lastSyncedAt: syncedAt,
      }),
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("visibility_setup_needed");
  });

  it("overlays publish result only when preferPublishOverlay is true", () => {
    const publishResult = {
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
    };

    const withoutOverlay = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: null }),
      publishResult,
      preferPublishOverlay: false,
      draftUpdatedAt: syncedAt,
    });
    expect(withoutOverlay.kind).toBe("added_to_shopify");

    const withOverlay = deriveProductLifecycle({
      syncedListing: null,
      publishResult,
      preferPublishOverlay: true,
    });
    expect(withOverlay.hasSyncedListing).toBe(true);
    expect(withOverlay.onlineStorePublished).toBe(true);
  });
});

describe("deriveListingsQueryLifecycleFlags", () => {
  it("treats undefined data as unconfirmed initial loading while fetching", () => {
    const flags = deriveListingsQueryLifecycleFlags({
      isPending: true,
      isFetching: true,
      isError: false,
      isRefetchError: false,
      data: undefined,
    });
    expect(flags.listingsHasConfirmedData).toBe(false);
    expect(flags.listingsInitialLoading).toBe(true);
    expect(flags.listingsInitialError).toBe(false);
  });

  it("treats empty array as confirmed success", () => {
    const flags = deriveListingsQueryLifecycleFlags({
      isPending: false,
      isFetching: false,
      isError: false,
      isRefetchError: false,
      data: [],
    });
    expect(flags.listingsHasConfirmedData).toBe(true);
    expect(flags.listingsInitialLoading).toBe(false);
  });

  it("treats initial error only when no data and not fetching", () => {
    const flags = deriveListingsQueryLifecycleFlags({
      isPending: false,
      isFetching: false,
      isError: true,
      isRefetchError: false,
      data: undefined,
    });
    expect(flags.listingsInitialError).toBe(true);
  });

  it("treats error with cached data as refresh failure", () => {
    const flags = deriveListingsQueryLifecycleFlags({
      isPending: false,
      isFetching: false,
      isError: true,
      isRefetchError: true,
      data: [syncedListing()],
    });
    expect(flags.listingsRefreshFailed).toBe(true);
    expect(flags.listingsInitialError).toBe(false);
    expect(flags.listingsFetching).toBe(false);
  });
});

describe("deriveProductLifecycle — listings loading and error truth", () => {
  const confirmedEmpty = { listingsHasConfirmedData: true, syncedListing: null };

  it("shows checking on initial load without confirmed data", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsInitialLoading: true,
    });
    expect(view.kind).toBe("loading");
    expect(view.badgeLabel).toBe("Checking Shopify status…");
  });

  it("shows unavailable on initial error without cached listing", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsInitialError: true,
    });
    expect(view.kind).toBe("unavailable");
    expect(view.badgeLabel).toBe("Shopify status unavailable");
    expect(view.showListingsRetry).toBe(true);
  });

  it("shows draft only after confirmed empty listings response", () => {
    const view = deriveProductLifecycle(confirmedEmpty);
    expect(view.kind).toBe("draft_not_on_shopify");
    expect(view.badgeLabel).toBe("Draft — not on Shopify");
  });

  it("never maps API error to draft", () => {
    const view = deriveProductLifecycle({
      syncedListing: null,
      listingsInitialError: true,
    });
    expect(view.kind).not.toBe("draft_not_on_shopify");
  });

  it("preserves synced badge while refreshing cached listing", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: true }),
      listingsHasConfirmedData: true,
      listingsFetching: true,
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("visible_on_shop");
    expect(view.statusNote).toBe("refreshing");
    expect(view.statusNoteMessage).toBe(REFRESHING_SHOPIFY_STATUS_COPY);
  });

  it("preserves visible badge when refresh fails with cached visibility true", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: true,
        lastSyncedAt: syncedAt,
      }),
      listingsHasConfirmedData: true,
      listingsRefreshFailed: true,
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("visible_on_shop");
    expect(view.statusNoteMessage).toBe(REFRESH_FAILED_SHOPIFY_STATUS_COPY);
    expect(view.showListingsRetry).toBe(true);
  });

  it("preserves added badge when refresh fails with unknown visibility", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: null }),
      listingsHasConfirmedData: true,
      listingsRefreshFailed: true,
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("added_to_shopify");
    expect(view.statusNoteMessage).toBe(REFRESH_FAILED_SHOPIFY_STATUS_COPY);
  });

  it("retry recovery to empty listings shows draft", () => {
    const view = deriveProductLifecycle({
      ...confirmedEmpty,
      listingsInitialError: false,
    });
    expect(view.kind).toBe("draft_not_on_shopify");
  });

  it("retry recovery to synced listing shows added to Shopify", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ onlineStorePublished: null }),
      listingsHasConfirmedData: true,
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("added_to_shopify");
  });

  it("retry recovery with visibility true shows visible on shop", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({
        onlineStorePublished: true,
        lastSyncedAt: syncedAt,
      }),
      listingsHasConfirmedData: true,
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("visible_on_shop");
  });

  it("preserves unsaved changes during refresh failure", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing(),
      listingsHasConfirmedData: true,
      listingsRefreshFailed: true,
      dirty: true,
      draftUpdatedAt: syncedAt,
    });
    expect(view.kind).toBe("unsaved_changes");
    expect(view.statusNoteMessage).toBe(REFRESH_FAILED_SHOPIFY_STATUS_COPY);
  });

  it("preserves saved-not-sent during refresh failure", () => {
    const view = deriveProductLifecycle({
      syncedListing: syncedListing({ lastSyncedAt: syncedAt }),
      listingsHasConfirmedData: true,
      listingsRefreshFailed: true,
      draftUpdatedAt: "2026-09-12T11:00:00.000Z",
      dirty: false,
    });
    expect(view.kind).toBe("changes_not_sent");
    expect(view.statusNoteMessage).toBe(REFRESH_FAILED_SHOPIFY_STATUS_COPY);
  });

  it("never emits Up to date on Shopify across loading and error paths", () => {
    const scenarios = [
      deriveProductLifecycle({
        syncedListing: null,
        listingsInitialLoading: true,
      }),
      deriveProductLifecycle({
        syncedListing: null,
        listingsInitialError: true,
      }),
      deriveProductLifecycle(confirmedEmpty),
      deriveProductLifecycle({
        syncedListing: syncedListing(),
        listingsHasConfirmedData: true,
        listingsRefreshFailed: true,
        draftUpdatedAt: syncedAt,
      }),
    ];
    for (const view of scenarios) {
      expect(view.badgeLabel).not.toMatch(/up to date on shopify/i);
      expect(view.supportingCopy).not.toMatch(/up to date on shopify/i);
    }
  });
});
