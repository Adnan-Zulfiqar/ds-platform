import { describe, expect, it } from "vitest";

import {
  deriveListingLifecycle,
  draftNewerThanSync,
  findSyncedListing,
} from "@/lib/listing-lifecycle";
import type { StoreListing } from "@/types/api";

/**
 * The listing authority shared by the product page, the catalogue and the
 * editor (UX-L2D-04, unit-tested in UX-L2D-05). Adapted from the reviewed
 * historical `deriveListingsQueryLifecycleFlags` cases.
 */

function listing(overrides: Partial<StoreListing> = {}): StoreListing {
  return {
    id: "l1",
    storeId: "s1",
    productId: "p1",
    externalProductId: "1",
    externalHandle: null,
    externalGraphqlId: null,
    shopDomain: null,
    storefrontUrl: null,
    adminUrl: null,
    onlineStorePublished: null,
    status: "synced",
    lastSyncedAt: null,
    lastError: null,
    publishedAt: null,
    lastFailedSyncAt: null,
    ...overrides,
  };
}

describe("deriveListingLifecycle", () => {
  it("undefined data while fetching is checking", () => {
    expect(
      deriveListingLifecycle({ data: undefined, isPending: true, isFetching: true, isError: false }).kind,
    ).toBe("checking");
  });

  it("undefined data after an error is unavailable, never not published", () => {
    const view = deriveListingLifecycle({ data: undefined, isPending: false, isFetching: false, isError: true });
    expect(view.kind).toBe("unavailable");
  });

  it("an empty array is a confirmed not published", () => {
    expect(deriveListingLifecycle({ data: [], isPending: false, isFetching: false, isError: false }).kind).toBe(
      "not-published",
    );
  });

  it("an error with cached data is a refresh failure that keeps the state", () => {
    const view = deriveListingLifecycle({
      data: [listing({ onlineStorePublished: true })],
      isPending: false,
      isFetching: false,
      isError: true,
    });
    expect(view.kind).toBe("visible-on-shop");
    expect(view.refreshFailed).toBe(true);
    expect(view.refreshing).toBe(false);
  });

  it("fetching with cached data is refreshing", () => {
    const view = deriveListingLifecycle({
      data: [listing()],
      isPending: false,
      isFetching: true,
      isError: false,
    });
    expect(view.kind).toBe("added-to-shopify");
    expect(view.refreshing).toBe(true);
  });

  it("only a synced row counts, whatever its position", () => {
    expect(findSyncedListing([listing({ status: "error" }), listing({ id: "l2" })])?.id).toBe("l2");
    expect(findSyncedListing([listing({ status: "pending" })])).toBeNull();
    expect(findSyncedListing(undefined)).toBeNull();
  });
});

describe("draftNewerThanSync", () => {
  it("is null without both timestamps or with an unparsable one", () => {
    expect(draftNewerThanSync(null, "2026-09-15T10:00:00Z")).toBeNull();
    expect(draftNewerThanSync("2026-09-15T10:00:00Z", null)).toBeNull();
    expect(draftNewerThanSync("not a date", "2026-09-15T10:00:00Z")).toBeNull();
  });

  it("is true only when the draft is strictly newer", () => {
    expect(draftNewerThanSync("2026-09-15T10:00:00.001Z", "2026-09-15T10:00:00.000Z")).toBe(true);
    expect(draftNewerThanSync("2026-09-15T10:00:00.000Z", "2026-09-15T10:00:00.000Z")).toBe(false);
    expect(draftNewerThanSync("2026-09-15T09:00:00Z", "2026-09-15T10:00:00Z")).toBe(false);
  });
});
