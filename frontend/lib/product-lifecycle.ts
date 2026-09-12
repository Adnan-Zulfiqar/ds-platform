import type { ShopifyPublishResult, StoreListing } from "@/types/api";

/** Authoritative lifecycle states derived from server listing fields only. */
export type ProductLifecycleKind =
  | "loading"
  | "unavailable"
  | "draft_not_on_shopify"
  | "added_to_shopify"
  | "visibility_setup_needed"
  | "visible_on_shop"
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
}

export interface DeriveProductLifecycleInput {
  listingsLoading?: boolean;
  listingsError?: boolean;
  syncedListing: StoreListing | null;
  publishResult?: ShopifyPublishResult | null;
  dirty?: boolean;
  publishPending?: boolean;
  publishFailed?: boolean;
}

function resolveListingFields(
  syncedListing: StoreListing | null,
  publishResult?: ShopifyPublishResult | null,
): Pick<
  StoreListing,
  "status" | "onlineStorePublished" | "storefrontUrl" | "adminUrl" | "shopDomain"
> & { externalProductId?: string; externalHandle?: string | null } {
  if (publishResult) {
    return {
      status: "synced",
      onlineStorePublished: publishResult.onlineStorePublished,
      storefrontUrl: publishResult.storefrontUrl,
      adminUrl: publishResult.adminUrl,
      shopDomain: publishResult.shopDomain,
      externalProductId: publishResult.externalProductId,
      externalHandle: publishResult.externalHandle,
    };
  }
  if (!syncedListing) {
    return {
      status: "none",
      onlineStorePublished: null,
      storefrontUrl: null,
      adminUrl: null,
      shopDomain: null,
    };
  }
  return syncedListing;
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
    };
  }

  const fields = resolveListingFields(syncedListing, publishResult);
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
    };
  }

  const online = fields.onlineStorePublished ?? null;
  const storefrontUrl = fields.storefrontUrl ?? null;
  const adminUrl = fields.adminUrl ?? null;

  if (dirty) {
    return {
      kind: "changes_not_sent",
      badgeLabel: "Changes not sent to Shopify",
      supportingCopy:
        "Your changes are saved in DropPilot but have not been sent to Shopify.",
      hasSyncedListing: true,
      onlineStorePublished: online,
      storefrontUrl,
      adminUrl,
    };
  }

  if (online === true) {
    return {
      kind: "visible_on_shop",
      badgeLabel: "Visible on your shop",
      supportingCopy: "This product is on Shopify and confirmed visible on your online shop.",
      hasSyncedListing: true,
      onlineStorePublished: true,
      storefrontUrl,
      adminUrl,
    };
  }

  if (online === false) {
    return {
      kind: "visibility_setup_needed",
      badgeLabel: "Added to Shopify",
      supportingCopy:
        "Added to Shopify, but it may not be visible on your online shop yet.",
      hasSyncedListing: true,
      onlineStorePublished: false,
      storefrontUrl,
      adminUrl,
    };
  }

  return {
    kind: "added_to_shopify",
    badgeLabel: "Added to Shopify",
    supportingCopy:
      "Your product is in Shopify. Open Shopify to check how it appears in your shop.",
    hasSyncedListing: true,
    onlineStorePublished: null,
    storefrontUrl,
    adminUrl,
  };
}

/** Clean synced state when listing exists, not dirty, and not in transient publish UI. */
export function deriveUpToDateLifecycle(
  input: DeriveProductLifecycleInput,
): ProductLifecycleView | null {
  const base = deriveProductLifecycle(input);
  if (
    base.hasSyncedListing &&
    !input.dirty &&
    !input.publishPending &&
    base.kind !== "publish_failed"
  ) {
    if (base.kind === "visible_on_shop") {
      return {
        ...base,
        kind: "up_to_date_on_shopify",
        badgeLabel: "Up to date on Shopify",
        supportingCopy: "Your latest saved version matches what is on Shopify.",
      };
    }
    if (base.kind === "added_to_shopify" || base.kind === "visibility_setup_needed") {
      return base;
    }
  }
  return null;
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
  };
}
