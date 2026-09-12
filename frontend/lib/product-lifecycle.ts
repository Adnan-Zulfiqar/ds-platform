import type { ShopifyPublishResult, StoreListing } from "@/types/api";

/** Authoritative lifecycle states derived from server listing fields only. */
export type ProductLifecycleKind =
  | "loading"
  | "unavailable"
  | "draft_not_on_shopify"
  | "added_to_shopify"
  | "visibility_setup_needed"
  | "visible_on_shop"
  | "unsaved_changes"
  | "changes_not_sent"
  | "publishing"
  | "publish_failed";

export type ShopifyStatusNote = "none" | "refreshing" | "refresh_failed";

export interface ProductLifecycleView {
  kind: ProductLifecycleKind;
  /** Short badge label for headers and tables. */
  badgeLabel: string;
  /** One-line supporting copy for panels and summaries. */
  supportingCopy: string;
  /** Whether a synced Shopify listing exists (status === synced). */
  hasSyncedListing: boolean;
  /** Server-confirmed online-store visibility — never inferred from handle/ID alone. */
  onlineStorePublished: boolean | null;
  /** Trusted public product URL from the server, if any. */
  storefrontUrl: string | null;
  adminUrl: string | null;
  /** True when saved draft is newer than last confirmed Shopify sync. */
  hasUnsentShopifyChanges: boolean;
  /** Secondary note when cached status is refreshing or refresh failed. */
  statusNote: ShopifyStatusNote;
  statusNoteMessage: string | null;
  /** Show Try again for listings fetch failures. */
  showListingsRetry: boolean;
}

export interface ListingsQueryLifecycleFlags {
  listingsHasConfirmedData: boolean;
  listingsInitialLoading: boolean;
  listingsInitialError: boolean;
  listingsFetching: boolean;
  listingsRefreshFailed: boolean;
}

export interface DeriveProductLifecycleInput {
  /** Successful listings response received (including an empty array). */
  listingsHasConfirmedData?: boolean;
  /** First fetch in progress with no confirmed response yet. */
  listingsInitialLoading?: boolean;
  /** First fetch failed with no confirmed response. */
  listingsInitialError?: boolean;
  /** Background refetch while cached listing data is shown. */
  listingsFetching?: boolean;
  /** Refetch failed while cached listing data remains. */
  listingsRefreshFailed?: boolean;
  /** @deprecated Use listingsInitialLoading */
  listingsLoading?: boolean;
  /** @deprecated Use listingsInitialError */
  listingsError?: boolean;
  syncedListing: StoreListing | null;
  /** Ephemeral publish response — overlay only while listings have not refetched. */
  publishResult?: ShopifyPublishResult | null;
  preferPublishOverlay?: boolean;
  draftUpdatedAt?: string | null;
  dirty?: boolean;
  publishPending?: boolean;
  publishFailed?: boolean;
}

export type DraftShopifySyncComparison = "unsent" | "not_unsent" | "unknown";

/** Copy when a listing exists but full draft-to-Shopify sync cannot be proven. */
export const CONSERVATIVE_SHOPIFY_SYNC_COPY =
  "Your DropPilot draft may contain changes that have not been sent to Shopify.";

export const REFRESHING_SHOPIFY_STATUS_COPY = "Refreshing Shopify status…";
export const REFRESH_FAILED_SHOPIFY_STATUS_COPY =
  "Couldn't refresh Shopify status";

/**
 * Map a React Query listings result to lifecycle authority inputs.
 * Never treat `isError` with cached data as an empty listings success.
 */
export function deriveListingsQueryLifecycleFlags(query: {
  isPending: boolean;
  isFetching: boolean;
  isError: boolean;
  isRefetchError: boolean;
  data: StoreListing[] | undefined;
}): ListingsQueryLifecycleFlags {
  const listingsHasConfirmedData = query.data !== undefined;
  const listingsInitialLoading =
    query.isFetching && !listingsHasConfirmedData;
  const listingsInitialError =
    query.isError && !listingsHasConfirmedData && !query.isFetching;
  const listingsRefreshFailed =
    listingsHasConfirmedData && query.isError && !query.isFetching;
  return {
    listingsHasConfirmedData,
    listingsInitialLoading,
    listingsInitialError,
    listingsFetching:
      query.isFetching && listingsHasConfirmedData && !listingsRefreshFailed,
    listingsRefreshFailed,
  };
}

/**
 * Compare the saved draft version to the last confirmed Shopify sync timestamp.
 *
 * `not_unsent` means the saved draft is not provably newer than `lastSyncedAt`.
 * It does **not** prove the complete draft was sent through the full Shopify
 * product publish path — inventory and price pushes also advance `lastSyncedAt`.
 */
export function compareDraftToShopifySync(
  draftUpdatedAt: string | null | undefined,
  lastSyncedAt: string | null | undefined,
): DraftShopifySyncComparison {
  if (!draftUpdatedAt || !lastSyncedAt) return "unknown";
  const draftMs = Date.parse(draftUpdatedAt);
  const syncMs = Date.parse(lastSyncedAt);
  if (Number.isNaN(draftMs) || Number.isNaN(syncMs)) return "unknown";
  if (draftMs > syncMs) return "unsent";
  return "not_unsent";
}

export function hasUnsentShopifyChanges(
  draftUpdatedAt: string | null | undefined,
  lastSyncedAt: string | null | undefined,
): boolean {
  return compareDraftToShopifySync(draftUpdatedAt, lastSyncedAt) === "unsent";
}

function resolveListingFields(
  syncedListing: StoreListing | null,
  publishResult?: ShopifyPublishResult | null,
  preferPublishOverlay = false,
): Pick<
  StoreListing,
  "status" | "onlineStorePublished" | "storefrontUrl" | "adminUrl" | "shopDomain" | "lastSyncedAt"
> {
  if (preferPublishOverlay && publishResult) {
    return {
      status: "synced",
      onlineStorePublished: publishResult.onlineStorePublished,
      storefrontUrl: publishResult.storefrontUrl,
      adminUrl: publishResult.adminUrl,
      shopDomain: publishResult.shopDomain,
      lastSyncedAt: new Date().toISOString(),
    };
  }
  if (syncedListing) {
    return syncedListing;
  }
  if (!publishResult) {
    return {
      status: "none",
      onlineStorePublished: null,
      storefrontUrl: null,
      adminUrl: null,
      shopDomain: null,
      lastSyncedAt: null,
    };
  }
  return {
    status: "synced",
    onlineStorePublished: publishResult.onlineStorePublished,
    storefrontUrl: publishResult.storefrontUrl,
    adminUrl: publishResult.adminUrl,
    shopDomain: publishResult.shopDomain,
    lastSyncedAt: null,
  };
}

function baseSyncedView(
  kind: ProductLifecycleKind,
  badgeLabel: string,
  supportingCopy: string,
  online: boolean | null,
  storefrontUrl: string | null,
  adminUrl: string | null,
  hasUnsent: boolean,
): ProductLifecycleView {
  return {
    kind,
    badgeLabel,
    supportingCopy,
    hasSyncedListing: true,
    onlineStorePublished: online,
    storefrontUrl,
    adminUrl,
    hasUnsentShopifyChanges: hasUnsent,
    statusNote: "none",
    statusNoteMessage: null,
    showListingsRetry: false,
  };
}

function visibilitySyncedView(
  online: boolean | null,
  storefrontUrl: string | null,
  adminUrl: string | null,
): ProductLifecycleView {
  if (online === true) {
    return baseSyncedView(
      "visible_on_shop",
      "Visible on your shop",
      "This product is on Shopify and confirmed visible on your online shop.",
      true,
      storefrontUrl,
      adminUrl,
      false,
    );
  }

  if (online === false) {
    return baseSyncedView(
      "visibility_setup_needed",
      "Added to Shopify",
      "Added to Shopify, but it may not be visible on your online shop yet.",
      false,
      storefrontUrl,
      adminUrl,
      false,
    );
  }

  return baseSyncedView(
    "added_to_shopify",
    "Added to Shopify",
    CONSERVATIVE_SHOPIFY_SYNC_COPY,
    null,
    storefrontUrl,
    adminUrl,
    false,
  );
}

function withStatusNote(
  view: ProductLifecycleView,
  note: ShopifyStatusNote,
  message: string | null,
  showRetry: boolean,
): ProductLifecycleView {
  return {
    ...view,
    statusNote: note,
    statusNoteMessage: message,
    showListingsRetry: showRetry || view.showListingsRetry,
  };
}

function loadingView(): ProductLifecycleView {
  return {
    kind: "loading",
    badgeLabel: "Checking Shopify status…",
    supportingCopy: "Loading store listing information.",
    hasSyncedListing: false,
    onlineStorePublished: null,
    storefrontUrl: null,
    adminUrl: null,
    hasUnsentShopifyChanges: false,
    statusNote: "none",
    statusNoteMessage: null,
    showListingsRetry: false,
  };
}

function unavailableView(): ProductLifecycleView {
  return {
    kind: "unavailable",
    badgeLabel: "Shopify status unavailable",
    supportingCopy: "We could not load Shopify status for this product.",
    hasSyncedListing: false,
    onlineStorePublished: null,
    storefrontUrl: null,
    adminUrl: null,
    hasUnsentShopifyChanges: false,
    statusNote: "none",
    statusNoteMessage: null,
    showListingsRetry: true,
  };
}

function normalizeListingsInput(input: DeriveProductLifecycleInput): ListingsQueryLifecycleFlags {
  const hasConfirmed = input.listingsHasConfirmedData ?? false;
  return {
    listingsHasConfirmedData: hasConfirmed,
    listingsInitialLoading:
      input.listingsInitialLoading ??
      (input.listingsLoading === true && !hasConfirmed),
    listingsInitialError:
      input.listingsInitialError ??
      (input.listingsError === true && !hasConfirmed),
    listingsFetching: input.listingsFetching ?? false,
    listingsRefreshFailed: input.listingsRefreshFailed ?? false,
  };
}

function deriveConfirmedLifecycle(
  input: DeriveProductLifecycleInput,
): ProductLifecycleView {
  const {
    syncedListing,
    publishResult,
    preferPublishOverlay = false,
    draftUpdatedAt = null,
    dirty = false,
    publishPending = false,
    publishFailed = false,
  } = input;

  if (publishPending) {
    return {
      kind: "publishing",
      badgeLabel: "Publishing…",
      supportingCopy: "Sending this product to Shopify.",
      hasSyncedListing: Boolean(syncedListing?.status === "synced"),
      onlineStorePublished: null,
      storefrontUrl: null,
      adminUrl: null,
      hasUnsentShopifyChanges: false,
      statusNote: "none",
      statusNoteMessage: null,
      showListingsRetry: false,
    };
  }

  if (publishFailed && !publishResult && syncedListing?.status !== "synced") {
    return {
      kind: "publish_failed",
      badgeLabel: "Publish failed",
      supportingCopy: "Publishing did not complete. Review the message and try again.",
      hasSyncedListing: false,
      onlineStorePublished: null,
      storefrontUrl: null,
      adminUrl: null,
      hasUnsentShopifyChanges: false,
      statusNote: "none",
      statusNoteMessage: null,
      showListingsRetry: false,
    };
  }

  const fields = resolveListingFields(syncedListing, publishResult, preferPublishOverlay);
  const hasSyncedListing = fields.status === "synced";

  if (!hasSyncedListing) {
    return {
      kind: "draft_not_on_shopify",
      badgeLabel: "Draft — not on Shopify",
      supportingCopy: "This product is saved in DropPilot but not on Shopify yet.",
      hasSyncedListing: false,
      onlineStorePublished: null,
      storefrontUrl: null,
      adminUrl: null,
      hasUnsentShopifyChanges: false,
      statusNote: "none",
      statusNoteMessage: null,
      showListingsRetry: false,
    };
  }

  const online = fields.onlineStorePublished ?? null;
  const storefrontUrl = fields.storefrontUrl ?? null;
  const adminUrl = fields.adminUrl ?? null;
  const syncComparison = compareDraftToShopifySync(
    draftUpdatedAt,
    fields.lastSyncedAt ?? syncedListing?.lastSyncedAt ?? null,
  );
  const unsent = syncComparison === "unsent";

  if (dirty) {
    return baseSyncedView(
      "unsaved_changes",
      "Unsaved changes",
      "You have unsaved edits in your browser. Save to keep them in DropPilot.",
      online,
      storefrontUrl,
      adminUrl,
      true,
    );
  }

  if (unsent) {
    return baseSyncedView(
      "changes_not_sent",
      "Changes saved in DropPilot — not sent to Shopify",
      "Your changes are saved in DropPilot but have not been sent to Shopify.",
      online,
      storefrontUrl,
      adminUrl,
      true,
    );
  }

  return visibilitySyncedView(online, storefrontUrl, adminUrl);
}

/**
 * Single frontend authority for draft vs Shopify lifecycle presentation.
 *
 * Visibility is confirmed only when `onlineStorePublished === true`. Listing
 * existence, handles, external IDs, and green styling never imply "live".
 */
export function deriveProductLifecycle(
  input: DeriveProductLifecycleInput,
): ProductLifecycleView {
  const listings = normalizeListingsInput(input);
  const {
    syncedListing,
    publishResult,
    preferPublishOverlay = false,
  } = input;

  const hasOverlayListing = Boolean(
    preferPublishOverlay && publishResult,
  );

  if (
    listings.listingsInitialLoading &&
    !listings.listingsHasConfirmedData &&
    !syncedListing &&
    !hasOverlayListing
  ) {
    return loadingView();
  }

  if (
    listings.listingsInitialError &&
    !listings.listingsHasConfirmedData &&
    !hasOverlayListing
  ) {
    return unavailableView();
  }

  if (
    !listings.listingsHasConfirmedData &&
    !syncedListing &&
    !hasOverlayListing
  ) {
    return loadingView();
  }

  const core = deriveConfirmedLifecycle(input);

  if (listings.listingsFetching && listings.listingsHasConfirmedData) {
    return withStatusNote(
      core,
      "refreshing",
      REFRESHING_SHOPIFY_STATUS_COPY,
      false,
    );
  }

  if (listings.listingsRefreshFailed && listings.listingsHasConfirmedData) {
    return withStatusNote(
      core,
      "refresh_failed",
      REFRESH_FAILED_SHOPIFY_STATUS_COPY,
      true,
    );
  }

  return core;
}
