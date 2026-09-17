import { describe, expect, it } from "vitest";

import {
  deriveEditorLifecycle,
  deriveNextAction,
  deriveSaveState,
  deriveShopifyState,
  type EditorLifecycleInput,
} from "@/lib/editor-lifecycle";
import type { ShopifyPublishResult, StoreListing } from "@/types/api";

/**
 * Scenario tests for the editor's lifecycle authority (UX-L2D-05). Adapted
 * from the reviewed historical `product-lifecycle.test.ts`: the sync-authority
 * and loading/error-truth cases are kept in spirit; the inputs are this
 * module's explicit ones and the publish overlay is keyed by timestamps.
 */

const T0 = Date.parse("2026-09-15T10:00:00.000Z");
const iso = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

function listing(overrides: Partial<StoreListing> = {}): StoreListing {
  return {
    id: "listing-1",
    storeId: "store-1",
    productId: "product-1",
    externalProductId: "8123456789",
    externalHandle: "lamp",
    externalGraphqlId: "gid://shopify/Product/8123456789",
    shopDomain: "demo-shop.myshopify.com",
    storefrontUrl: "https://demo-shop.myshopify.com/products/lamp",
    adminUrl: "https://demo-shop.myshopify.com/admin/products/8123456789",
    onlineStorePublished: null,
    status: "synced",
    lastSyncedAt: iso(0),
    lastError: null,
    publishedAt: null,
    lastFailedSyncAt: null,
    ...overrides,
  };
}

function publishResult(overrides: Partial<ShopifyPublishResult> = {}): ShopifyPublishResult {
  return {
    message: "Published.",
    listingId: "listing-1",
    externalProductId: "8123456789",
    externalHandle: "lamp",
    externalGraphqlId: null,
    shopDomain: "demo-shop.myshopify.com",
    storefrontUrl: "https://demo-shop.myshopify.com/products/lamp",
    adminUrl: "https://demo-shop.myshopify.com/admin/products/8123456789",
    onlineStorePublished: null,
    updated: true,
    ...overrides,
  };
}

/** A settled editor: listings confirmed (given rows), nothing in flight. */
function input(
  rows: StoreListing[] | undefined,
  overrides: Partial<EditorLifecycleInput> = {},
): EditorLifecycleInput {
  return {
    productStatus: "draft",
    dirty: false,
    saveState: "idle",
    conflict: false,
    publishPending: false,
    publishFailed: false,
    publishResult: null,
    publishResultAt: null,
    listings: {
      data: rows,
      isPending: rows === undefined,
      isFetching: false,
      isError: false,
      dataUpdatedAt: rows === undefined ? 0 : T0 - 60_000,
    },
    draftUpdatedAt: iso(-3_600_000),
    issueCount: 0,
    ...overrides,
  };
}

describe("deriveShopifyState — listing authority", () => {
  it("reads a confirmed empty response as not on Shopify", () => {
    const state = deriveShopifyState(input([]));
    expect(state.kind).toBe("not-on-shopify");
    expect(state.hasSyncedListing).toBe(false);
    expect(state.label).toBe("Not on Shopify");
  });

  it("never turns a failed initial load into not on Shopify", () => {
    const state = deriveShopifyState(
      input(undefined, {
        listings: { data: undefined, isPending: false, isFetching: false, isError: true, dataUpdatedAt: 0 },
      }),
    );
    expect(state.kind).toBe("unavailable");
    expect(state.retry).toBe(true);
    expect(state.label).not.toMatch(/not on shopify|draft/i);
  });

  it("reports checking while the first request is in flight", () => {
    const state = deriveShopifyState(
      input(undefined, {
        listings: { data: undefined, isPending: true, isFetching: true, isError: false, dataUpdatedAt: 0 },
      }),
    );
    expect(state.kind).toBe("checking");
  });

  it("a synced listing without visibility proof is added, not visible", () => {
    const state = deriveShopifyState(input([listing({ onlineStorePublished: null })]));
    expect(state.kind).toBe("added-to-shopify");
    expect(state.label).toBe("Added to Shopify");
    expect(state.hasSyncedListing).toBe(true);
    expect(state.onlineStorePublished).toBeNull();
    expect(state.label).not.toMatch(/live|visible/i);
  });

  it("visible on your shop only when onlineStorePublished is true", () => {
    const state = deriveShopifyState(input([listing({ onlineStorePublished: true })]));
    expect(state.kind).toBe("visible-on-shop");
    expect(state.storefrontUrl).toBe("https://demo-shop.myshopify.com/products/lamp");
  });

  it("onlineStorePublished false is visibility setup needed, labelled as added", () => {
    const state = deriveShopifyState(input([listing({ onlineStorePublished: false })]));
    expect(state.kind).toBe("visibility-setup-needed");
    expect(state.label).toBe("Added to Shopify");
    expect(state.detail).toMatch(/not visible/i);
  });

  it("drops storefront and admin links that are not HTTPS *.myshopify.com", () => {
    const state = deriveShopifyState(
      input([
        listing({
          onlineStorePublished: true,
          storefrontUrl: "http://demo-shop.myshopify.com/products/lamp",
          adminUrl: "https://evil.example.com/admin",
        }),
      ]),
    );
    expect(state.kind).toBe("visible-on-shop");
    expect(state.storefrontUrl).toBeNull();
    expect(state.adminUrl).toBeNull();
  });

  it("an errored row with no synced row is publish failed, keeping the admin link", () => {
    const state = deriveShopifyState(
      input([listing({ status: "error", lastError: "Shopify said no", onlineStorePublished: null })]),
    );
    expect(state.kind).toBe("publish-failed");
    expect(state.hasSyncedListing).toBe(false);
    expect(state.adminUrl).toBe("https://demo-shop.myshopify.com/admin/products/8123456789");
    // Raw provider text never reaches the merchant.
    expect(state.detail).not.toContain("Shopify said no");
  });

  it("pending and removed rows do not count as on Shopify", () => {
    expect(deriveShopifyState(input([listing({ status: "pending" })])).kind).toBe("not-on-shopify");
    expect(deriveShopifyState(input([listing({ status: "removed" })])).kind).toBe("not-on-shopify");
  });

  it("archived wins over everything", () => {
    const state = deriveShopifyState(
      input([listing({ onlineStorePublished: true })], { productStatus: "archived", publishPending: true }),
    );
    expect(state.kind).toBe("archived");
  });
});

describe("deriveShopifyState — lastSyncedAt is not 'up to date'", () => {
  it("a saved draft provably newer than the last sync is changes not sent", () => {
    const state = deriveShopifyState(
      input([listing({ lastSyncedAt: iso(-7_200_000) })], { draftUpdatedAt: iso(-3_600_000) }),
    );
    expect(state.kind).toBe("changes-not-sent");
    expect(state.unsentChanges).toBe(true);
    expect(state.hasSyncedListing).toBe(true);
  });

  it("a draft older than the last sync is still only 'added', never 'up to date'", () => {
    const state = deriveShopifyState(
      input([listing({ lastSyncedAt: iso(0) })], { draftUpdatedAt: iso(-3_600_000) }),
    );
    expect(state.kind).toBe("added-to-shopify");
    expect(state.unsentChanges).toBe(false);
    expect(state.label).not.toMatch(/up to date|live/i);
  });

  it("an inventory-only push that advanced lastSyncedAt does not hide a later edit", () => {
    // Edit at T-30min, inventory push at T-60min: the edit is newer.
    const state = deriveShopifyState(
      input([listing({ lastSyncedAt: iso(-3_600_000) })], { draftUpdatedAt: iso(-1_800_000) }),
    );
    expect(state.kind).toBe("changes-not-sent");
  });

  it("an unknowable comparison falls back to the listing facts", () => {
    const state = deriveShopifyState(
      input([listing({ lastSyncedAt: null, onlineStorePublished: true })], { draftUpdatedAt: iso(0) }),
    );
    expect(state.kind).toBe("visible-on-shop");
    expect(state.unsentChanges).toBeNull();
  });

  it("equal instants across time-zone spellings are not newer", () => {
    const state = deriveShopifyState(
      input([listing({ lastSyncedAt: "2026-09-15T10:00:00+00:00" })], {
        draftUpdatedAt: "2026-09-15T11:00:00+01:00",
      }),
    );
    expect(state.kind).toBe("added-to-shopify");
    expect(state.unsentChanges).toBe(false);
  });
});

describe("deriveShopifyState — refresh honesty", () => {
  it("keeps the last known state while a refresh runs", () => {
    const state = deriveShopifyState(
      input([listing({ onlineStorePublished: true })], {
        listings: {
          data: [listing({ onlineStorePublished: true })],
          isPending: false,
          isFetching: true,
          isError: false,
          dataUpdatedAt: T0 - 60_000,
        },
      }),
    );
    expect(state.kind).toBe("visible-on-shop");
    expect(state.refreshing).toBe(true);
    expect(state.note).toMatch(/refreshing/i);
    expect(state.retry).toBe(false);
  });

  it("keeps the last known state when a refresh fails, and says so", () => {
    const state = deriveShopifyState(
      input([listing()], {
        listings: {
          data: [listing()],
          isPending: false,
          isFetching: false,
          isError: true,
          dataUpdatedAt: T0 - 60_000,
        },
      }),
    );
    expect(state.kind).toBe("added-to-shopify");
    expect(state.refreshFailed).toBe(true);
    expect(state.note).toMatch(/couldn.t refresh/i);
    expect(state.retry).toBe(true);
  });
});

describe("deriveShopifyState — publishing and the publish-result overlay", () => {
  it("shows sending while a publish is in flight", () => {
    const state = deriveShopifyState(input([], { publishPending: true }));
    expect(state.kind).toBe("publishing");
    expect(state.label).toBe("Sending to Shopify…");
  });

  it("a failed publish in this session is publish failed", () => {
    const state = deriveShopifyState(input([], { publishFailed: true }));
    expect(state.kind).toBe("publish-failed");
  });

  it("a failed update keeps the synced listing's facts", () => {
    const state = deriveShopifyState(
      input([listing({ onlineStorePublished: true })], { publishFailed: true }),
    );
    expect(state.kind).toBe("publish-failed");
    expect(state.hasSyncedListing).toBe(true);
    expect(state.adminUrl).toBe("https://demo-shop.myshopify.com/admin/products/8123456789");
  });

  it("uses the publish response while the listings cache predates it", () => {
    const publishedAt = T0;
    const state = deriveShopifyState(
      input([], {
        publishResult: publishResult({ onlineStorePublished: true }),
        publishResultAt: publishedAt,
        listings: { data: [], isPending: false, isFetching: true, isError: false, dataUpdatedAt: publishedAt - 5_000 },
      }),
    );
    expect(state.kind).toBe("visible-on-shop");
    expect(state.hasSyncedListing).toBe(true);
    expect(state.unsentChanges).toBeNull();
  });

  it("defers to the server row once listings refetch after the publish", () => {
    const publishedAt = T0;
    const state = deriveShopifyState(
      input([listing({ onlineStorePublished: false, lastSyncedAt: iso(0) })], {
        publishResult: publishResult({ onlineStorePublished: true }),
        publishResultAt: publishedAt,
        listings: {
          data: [listing({ onlineStorePublished: false, lastSyncedAt: iso(0) })],
          isPending: false,
          isFetching: false,
          isError: false,
          dataUpdatedAt: publishedAt + 500,
        },
      }),
    );
    expect(state.kind).toBe("visibility-setup-needed");
  });

  it("a failed refetch after the publish keeps the overlay and says the refresh failed", () => {
    const publishedAt = T0;
    const state = deriveShopifyState(
      input([], {
        publishResult: publishResult({ onlineStorePublished: true }),
        publishResultAt: publishedAt,
        listings: { data: [], isPending: false, isFetching: false, isError: true, dataUpdatedAt: publishedAt - 5_000 },
      }),
    );
    expect(state.kind).toBe("visible-on-shop");
    expect(state.refreshFailed).toBe(true);
    expect(state.note).toMatch(/couldn.t refresh/i);
    expect(state.retry).toBe(true);
  });

  it("the overlay never claims visibility the response did not confirm", () => {
    const state = deriveShopifyState(
      input([], {
        publishResult: publishResult({ onlineStorePublished: null, storefrontUrl: null }),
        publishResultAt: T0,
        listings: { data: [], isPending: false, isFetching: true, isError: false, dataUpdatedAt: T0 - 1 },
      }),
    );
    expect(state.kind).toBe("added-to-shopify");
    expect(state.storefrontUrl).toBeNull();
  });
});

describe("deriveSaveState", () => {
  it("an in-flight save wins over dirty", () => {
    expect(deriveSaveState({ dirty: true, saveState: "saving", conflict: false }).kind).toBe("saving");
  });

  it("a failed save offers a retry", () => {
    const state = deriveSaveState({ dirty: true, saveState: "error", conflict: false });
    expect(state.kind).toBe("save-error");
    expect(state.retry).toBe(true);
  });

  it("a conflict is reported as paused saving, not as saved", () => {
    const state = deriveSaveState({ dirty: true, saveState: "conflict", conflict: true });
    expect(state.kind).toBe("conflict");
    expect(state.label).toMatch(/someone else saved this product/i);
    expect(state.label).not.toMatch(/saved in droppilot/i);
  });

  it("dirty is unsaved changes", () => {
    expect(deriveSaveState({ dirty: true, saveState: "saved", conflict: false }).kind).toBe("unsaved");
  });

  it("saved and clean both read as saved in DropPilot, never 'live'", () => {
    for (const saveState of ["saved", "idle"] as const) {
      const state = deriveSaveState({ dirty: false, saveState, conflict: false });
      expect(state.label).toBe("Saved in DropPilot");
      expect(state.label).not.toMatch(/live/i);
    }
  });
});

describe("deriveNextAction", () => {
  const settled = { conflict: false, publishPending: false, dirty: false, issueCount: 0 };

  it("a conflict asks to be resolved before anything else", () => {
    const shopify = deriveShopifyState(input([listing()]));
    expect(deriveNextAction({ ...settled, conflict: true, dirty: true }, shopify).kind).toBe("resolve-conflict");
  });

  it("publishing is disabled progress", () => {
    const shopify = deriveShopifyState(input([], { publishPending: true }));
    expect(deriveNextAction({ ...settled, publishPending: true }, shopify).kind).toBe("publishing");
  });

  it("a failed publish offers to try again", () => {
    const shopify = deriveShopifyState(input([], { publishFailed: true }));
    expect(deriveNextAction(settled, shopify).kind).toBe("retry-publish");
  });

  it("a published product with nothing to send offers View product", () => {
    const shopify = deriveShopifyState(input([listing()]));
    expect(deriveNextAction(settled, shopify).kind).toBe("view-product");
  });

  it("a published product with unsent or unsaved changes offers Update Shopify", () => {
    const unsent = deriveShopifyState(
      input([listing({ lastSyncedAt: iso(-7_200_000) })], { draftUpdatedAt: iso(0) }),
    );
    expect(deriveNextAction(settled, unsent).kind).toBe("update-shopify");
    const clean = deriveShopifyState(input([listing()]));
    expect(deriveNextAction({ ...settled, dirty: true }, clean).kind).toBe("update-shopify");
  });

  it("a draft with checklist items says Review N items", () => {
    const shopify = deriveShopifyState(input([]));
    expect(deriveNextAction({ ...settled, issueCount: 1 }, shopify)).toEqual({
      kind: "review-items",
      label: "Review 1 item",
    });
    expect(deriveNextAction({ ...settled, issueCount: 3 }, shopify).label).toBe("Review 3 items");
  });

  it("a draft with nothing flagged says Review & publish", () => {
    const shopify = deriveShopifyState(input([]));
    expect(deriveNextAction(settled, shopify).label).toBe("Review & publish");
  });
});

describe("deriveEditorLifecycle", () => {
  it("composes the three views from one input", () => {
    const lifecycle = deriveEditorLifecycle(input([listing()], { dirty: true }));
    expect(lifecycle.shopify.kind).toBe("added-to-shopify");
    expect(lifecycle.save.kind).toBe("unsaved");
    expect(lifecycle.next.kind).toBe("update-shopify");
  });

  it("no output ever reads 'live' or 'not live'", () => {
    const cases: EditorLifecycleInput[] = [
      input([]),
      input([listing()]),
      input([listing({ onlineStorePublished: true })]),
      input([listing({ onlineStorePublished: false })]),
      input([listing({ lastSyncedAt: iso(-7_200_000) })], { draftUpdatedAt: iso(0) }),
      input([], { publishPending: true }),
      input([], { publishFailed: true }),
      input(undefined, {
        listings: { data: undefined, isPending: false, isFetching: false, isError: true, dataUpdatedAt: 0 },
      }),
    ];
    for (const c of cases) {
      const lifecycle = deriveEditorLifecycle(c);
      for (const text of [
        lifecycle.shopify.label,
        lifecycle.shopify.detail,
        lifecycle.shopify.note ?? "",
        lifecycle.save.label,
        lifecycle.next.label,
      ]) {
        expect(text).not.toMatch(/\blive\b/i);
      }
    }
  });
});
