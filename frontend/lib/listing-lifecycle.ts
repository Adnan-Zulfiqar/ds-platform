import type { StoreListing } from "@/types/api";

/**
 * Where a product stands on Shopify, derived from server listing rows only.
 *
 * Selectively integrated from the reviewed historical `product-lifecycle.ts`
 * (UX-L2D-GATE-04): the listing-authority subset needed by the published
 * product page and the catalogue. The editor-side states of that module
 * (unsaved changes, publishing, publish failed, the publish-result overlay)
 * belong to the editor and are deferred to UX-L2D-05.
 *
 * Two rules from the review are kept verbatim in spirit:
 *
 * 1. **"Published" means a listing with `status === "synced"`** — the same
 *    predicate the backend uses to decide whether a product appears under
 *    Products (`ProductRepository._synced_listing_exists`). `Product.status`
 *    stays `draft` after a Shopify publish and is not consulted.
 * 2. **"Visible on your shop" only when `onlineStorePublished === true`.**
 *    A handle, an external id, or a storefront URL never implies visibility;
 *    `false` and `null` both read as "added to Shopify" with the caveat that it
 *    may not be visible yet.
 *
 * A failed listings request is reported as *unavailable*, never as "not
 * published": absence of evidence is not evidence of absence.
 */

export type ListingLifecycleKind =
  | "checking"
  | "unavailable"
  | "not-published"
  | "added-to-shopify"
  | "visible-on-shop";

export interface ListingLifecycle {
  kind: ListingLifecycleKind;
  /** Short label for a badge. */
  label: string;
  /** One sentence for a summary panel. */
  detail: string;
  /** The synced listing the view is built from, when there is one. */
  listing: StoreListing | null;
  /** Server-confirmed visibility; `null` when the server did not say. */
  onlineStorePublished: boolean | null;
  /** True while cached listings are being refreshed in the background. */
  refreshing: boolean;
  /** True when a refresh failed but cached listings are still shown. */
  refreshFailed: boolean;
}

export interface ListingsQueryState {
  data: StoreListing[] | undefined;
  isPending: boolean;
  isFetching: boolean;
  isError: boolean;
}

/** The listing a product is published through, if any. */
export function findSyncedListing(listings: StoreListing[] | undefined): StoreListing | null {
  return listings?.find((row) => row.status === "synced") ?? null;
}

export function deriveListingLifecycle(query: ListingsQueryState): ListingLifecycle {
  const hasData = query.data !== undefined;
  const base = {
    listing: null,
    onlineStorePublished: null,
    refreshing: hasData && query.isFetching && !query.isError,
    refreshFailed: hasData && query.isError && !query.isFetching,
  };

  if (!hasData && query.isFetching) {
    return {
      ...base,
      kind: "checking",
      label: "Checking Shopify status…",
      detail: "Loading store listing information.",
    };
  }
  if (!hasData) {
    return {
      ...base,
      kind: "unavailable",
      label: "Shopify status unavailable",
      detail: "We could not load Shopify status for this product.",
    };
  }

  const listing = findSyncedListing(query.data);
  if (!listing) {
    return {
      ...base,
      kind: "not-published",
      label: "Not on Shopify",
      detail: "This product is saved in DropPilot but has not been published to a store.",
    };
  }

  const online = listing.onlineStorePublished ?? null;
  if (online === true) {
    return {
      ...base,
      kind: "visible-on-shop",
      label: "Visible on your shop",
      detail: "This product is on Shopify and confirmed visible on your online shop.",
      listing,
      onlineStorePublished: true,
    };
  }
  return {
    ...base,
    kind: "added-to-shopify",
    label: "Added to Shopify",
    detail:
      online === false
        ? "Added to Shopify, but it may not be visible on your online shop yet."
        : "Added to Shopify. Visibility on your online shop has not been confirmed.",
    listing,
    onlineStorePublished: online,
  };
}

/**
 * Whether the saved draft is provably newer than the last confirmed Shopify
 * sync. "Not newer" does not prove the whole draft was sent — inventory and
 * price pushes also advance `lastSyncedAt` — so callers phrase the positive
 * case conservatively.
 */
export function draftNewerThanSync(
  draftUpdatedAt: string | null | undefined,
  lastSyncedAt: string | null | undefined,
): boolean | null {
  if (!draftUpdatedAt || !lastSyncedAt) return null;
  const draft = Date.parse(draftUpdatedAt);
  const synced = Date.parse(lastSyncedAt);
  if (Number.isNaN(draft) || Number.isNaN(synced)) return null;
  return draft > synced;
}
