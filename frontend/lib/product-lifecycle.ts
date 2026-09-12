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
  | "up_to_date_on_shopify"
  | "publishing"
  | "publish_failed";

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
}

export interface DeriveProductLifecycleInput {
  listingsLoading?: boolean;
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

export type DraftShopifySyncComparison = "unsent" | "up_to_date" | "unknown";

/**
 * Compare the saved draft version to the last confirmed Shopify sync.
 *
 * `dirty === false` proves the draft is saved in DropPilot; it does not prove
 * the saved version was sent to Shopify. Only a timestamp comparison can.
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
  return "up_to_date";
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
  };
}

function visibilitySyncedView(
  online: boolean | null,
  storefrontUrl: string | null,
  adminUrl: string | null,
  syncComparison: DraftShopifySyncComparison,
): ProductLifecycleView {
  const upToDate = syncComparison === "up_to_date";

  if (upToDate && online === true) {
    return baseSyncedView(
      "up_to_date_on_shopify",
      "Up to date on Shopify",
      "Your latest saved version matches what is on Shopify.",
      true,
      storefrontUrl,
      adminUrl,
      false,
    );
  }

  if (upToDate && online === false) {
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

  if (upToDate) {
    return baseSyncedView(
      "added_to_shopify",
      "Added to Shopify",
      "Your product is in Shopify. Open Shopify to check how it appears in your shop.",
      null,
      storefrontUrl,
      adminUrl,
      false,
    );
  }

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
    "Your DropPilot draft may contain changes that have not been sent to Shopify.",
    null,
    storefrontUrl,
    adminUrl,
    false,
  );
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
  const {
    listingsLoading = false,
    listingsError = false,
    syncedListing,
    publishResult,
    preferPublishOverlay = false,
    draftUpdatedAt = null,
    dirty = false,
    publishPending = false,
    publishFailed = false,
  } = input;

  if (listingsLoading && !publishResult && !syncedListing) {
    return {
      kind: "loading",
      badgeLabel: "Checking Shopify status…",
      supportingCopy: "Loading store listing information.",
      hasSyncedListing: false,
      onlineStorePublished: null,
      storefrontUrl: null,
      adminUrl: null,
      hasUnsentShopifyChanges: false,
    };
  }

  if (listingsError && !publishResult && !syncedListing) {
    return unavailableView();
  }

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

  return visibilitySyncedView(online, storefrontUrl, adminUrl, syncComparison);
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
  };
}
