import { isTrustedShopifyHttpsUrl } from "@/lib/external-link";
import {
  deriveListingLifecycle,
  draftNewerThanSync,
  type ListingLifecycle,
  type ListingsQueryState,
} from "@/lib/listing-lifecycle";
import type { ShopifyPublishResult, StoreListing } from "@/types/api";

/**
 * The one place the editor decides what to say about a product's lifecycle
 * (UX-L2D-05).
 *
 * Every surface in the editor — header badge, save indicator, primary
 * action, mobile bar, post-publish panel, Review & publish — reads the object
 * this module returns rather than re-deriving from the query flags. Before,
 * each of them ran its own predicate and the header could say "Draft — not
 * live" beside a panel saying "Published successfully".
 *
 * Adapted from the reviewed historical `product-lifecycle.ts`
 * (UX-L2D-GATE-04). Kept: the listing-authority rules, the loading/unavailable
 * honesty, the "unsent changes" comparison and its conservative wording.
 * Changed: the listing subset already lives in `listing-lifecycle.ts` (the
 * product page and catalogue use it), so this module composes it instead of
 * duplicating it; the publish-result overlay is bounded by the listings
 * cache timestamp rather than by an effect; and save state is a separate
 * axis instead of being folded into the same enum as Shopify state — the two
 * describe different places and are never contradictory side by side.
 *
 * Nothing here is inferred. Each state names the evidence it needs:
 *
 * | Shopify state           | Evidence                                                         |
 * |-------------------------|------------------------------------------------------------------|
 * | archived                | `product.status === "archived"`                                  |
 * | publishing              | a publish request is in flight                                   |
 * | publish-failed          | the last publish attempt in this session failed, or the only     |
 * |                         | listing row has `status === "error"` (no synced row)             |
 * | checking                | listings never loaded and a request is in flight                 |
 * | unavailable             | listings never loaded and the request failed                     |
 * | not-on-shopify          | listings confirmed with no `synced` row                          |
 * | changes-not-sent        | synced row, and the saved draft is *provably* newer than         |
 * |                         | `lastSyncedAt`                                                   |
 * | visible-on-shop         | synced row with `onlineStorePublished === true`                  |
 * | visibility-setup-needed | synced row with `onlineStorePublished === false`                 |
 * | added-to-shopify        | synced row with `onlineStorePublished === null`                  |
 *
 * `lastSyncedAt` is advanced by price and inventory pushes as well as by a
 * full publish, so "not provably newer" is never presented as "up to date on
 * Shopify" — the added/visible states describe the listing, not currency.
 */

export type ShopifyStateKind =
  | "archived"
  | "publishing"
  | "publish-failed"
  | "checking"
  | "unavailable"
  | "not-on-shopify"
  | "changes-not-sent"
  | "visible-on-shop"
  | "visibility-setup-needed"
  | "added-to-shopify";

export type StateTone = "neutral" | "info" | "success" | "warning" | "danger";

export interface ShopifyState {
  kind: ShopifyStateKind;
  /** Badge text. */
  label: string;
  /** One sentence a panel can show under the badge. */
  detail: string;
  tone: StateTone;
  /** A `synced` listing row exists (server or this session's publish result). */
  hasSyncedListing: boolean;
  /** Server-confirmed visibility; `null` when the server did not say. */
  onlineStorePublished: boolean | null;
  /** HTTPS `*.myshopify.com` URLs from the server, or `null`. Never built. */
  storefrontUrl: string | null;
  adminUrl: string | null;
  shopDomain: string | null;
  /** The listing the state was read from, when there is one. */
  listing: StoreListing | null;
  /** Saved draft provably newer than the last sync; `null` when unknowable. */
  unsentChanges: boolean | null;
  /** A background refresh of cached listings is running. */
  refreshing: boolean;
  /** Cached listings are shown but the latest refresh failed. */
  refreshFailed: boolean;
  /** Secondary line for the refresh/unavailable situations, else `null`. */
  note: string | null;
  /** Offer a listings retry. */
  retry: boolean;
}

export type SaveStateKind =
  | "saving"
  | "save-error"
  | "conflict"
  | "unsaved"
  | "saved"
  | "clean";

export interface SaveStateView {
  kind: SaveStateKind;
  label: string;
  tone: StateTone;
  /** Show a "Try again" affordance. */
  retry: boolean;
}

export type NextActionKind =
  | "resolve-conflict"
  | "publishing"
  | "retry-publish"
  | "update-shopify"
  | "view-product"
  | "review-items"
  | "review-publish";

export interface NextAction {
  kind: NextActionKind;
  label: string;
}

export interface EditorLifecycle {
  shopify: ShopifyState;
  save: SaveStateView;
  next: NextAction;
}

export type EditorSaveState = "idle" | "saving" | "saved" | "error" | "conflict";

export interface EditorLifecycleInput {
  productStatus: string;
  dirty: boolean;
  saveState: EditorSaveState;
  /** Any conflict phase other than `none`. */
  conflict: boolean;
  publishPending: boolean;
  /** A publish attempt in this session ended with an error that is still shown. */
  publishFailed: boolean;
  /** This session's last successful publish response, if any. */
  publishResult: ShopifyPublishResult | null;
  /** `Date.now()` when `publishResult` arrived; `null` without one. */
  publishResultAt: number | null;
  listings: ListingsQueryState & {
    /** React Query's `dataUpdatedAt`: when `data` was last confirmed. */
    dataUpdatedAt: number;
  };
  /** The version token last confirmed by the server (`savedUpdatedAt`). */
  draftUpdatedAt: string | null;
  /** Client-side checklist item count for the "Review N items" label. */
  issueCount: number;
}

function trusted(url: string | null | undefined): string | null {
  return isTrustedShopifyHttpsUrl(url) ? url : null;
}

/** The listing whose last attempt failed, when no synced row exists. */
function findFailedListing(listings: StoreListing[] | undefined): StoreListing | null {
  if (!listings) return null;
  if (listings.some((row) => row.status === "synced")) return null;
  return listings.find((row) => row.status === "error") ?? null;
}

/**
 * Whether the publish response should stand in for the listings cache.
 *
 * Only while the cache predates the response: `dataUpdatedAt` is when React
 * Query last confirmed `data`, so once a refetch completes after the publish
 * the server row is authoritative again and the overlay is inert — including
 * when that row disagrees with what the publish reported.
 */
function publishOverlayActive(input: EditorLifecycleInput): boolean {
  if (!input.publishResult || input.publishResultAt === null) return false;
  return input.listings.dataUpdatedAt < input.publishResultAt;
}

const EMPTY_LINKS = {
  storefrontUrl: null,
  adminUrl: null,
  shopDomain: null,
  listing: null,
  onlineStorePublished: null,
  hasSyncedListing: false,
  unsentChanges: null,
} as const;

function fromListing(listing: ListingLifecycle) {
  return {
    listing: listing.listing,
    onlineStorePublished: listing.onlineStorePublished,
    hasSyncedListing: listing.listing !== null,
    unsentChanges: null as boolean | null,
    storefrontUrl: trusted(listing.listing?.storefrontUrl),
    adminUrl: trusted(listing.listing?.adminUrl),
    shopDomain: listing.listing?.shopDomain ?? null,
  };
}

function visibilityState(
  online: boolean | null,
  base: Omit<ShopifyState, "kind" | "label" | "detail" | "tone">,
): ShopifyState {
  if (online === true) {
    return {
      ...base,
      kind: "visible-on-shop",
      label: "Visible on your shop",
      detail: "This product is on Shopify and confirmed visible on your online shop.",
      tone: "success",
    };
  }
  if (online === false) {
    return {
      ...base,
      kind: "visibility-setup-needed",
      label: "Added to Shopify",
      detail:
        "Shopify holds this product as a draft, so it is not visible on your online shop yet. Set it to Active in Shopify to show it.",
      tone: "info",
    };
  }
  return {
    ...base,
    kind: "added-to-shopify",
    label: "Added to Shopify",
    detail:
      "This product is on Shopify. Whether it is visible on your online shop has not been confirmed.",
    tone: "info",
  };
}

export function deriveShopifyState(input: EditorLifecycleInput): ShopifyState {
  const listing = deriveListingLifecycle(input.listings);
  const overlay = publishOverlayActive(input);
  const links = overlay ? { ...EMPTY_LINKS } : fromListing(listing);
  const base = {
    ...links,
    refreshing: listing.refreshing,
    refreshFailed: listing.refreshFailed,
    note: null as string | null,
    retry: false,
  };

  if (input.productStatus === "archived") {
    return {
      ...base,
      kind: "archived",
      label: "Archived",
      detail: "This product is archived in DropPilot.",
      tone: "neutral",
    };
  }

  if (input.publishPending) {
    return {
      ...base,
      kind: "publishing",
      label: "Sending to Shopify…",
      detail: "Publishing this product to your Shopify store.",
      tone: "info",
    };
  }

  const failedListing = overlay ? null : findFailedListing(input.listings.data);
  if (input.publishFailed || failedListing) {
    // A failed *update* of a product already on Shopify keeps the synced
    // row's facts (links, visibility); a failed first publish has only the
    // errored row to offer.
    return {
      ...base,
      listing: failedListing ?? base.listing,
      adminUrl: trusted(failedListing?.adminUrl) ?? base.adminUrl,
      shopDomain: failedListing?.shopDomain ?? base.shopDomain,
      kind: "publish-failed",
      label: "Publish failed",
      detail: input.publishFailed
        ? "Publishing did not complete. Review the message on Review & publish and try again."
        : "The last attempt to send this product to Shopify failed. Try publishing again.",
      tone: "danger",
    };
  }

  if (overlay && input.publishResult) {
    // The server's own publish response, shown until the listings cache
    // catches up. No `lastSyncedAt` here, so nothing is claimed about
    // unsent changes.
    const result = input.publishResult;
    return visibilityState(result.onlineStorePublished ?? null, {
      ...base,
      hasSyncedListing: true,
      onlineStorePublished: result.onlineStorePublished ?? null,
      storefrontUrl: trusted(result.storefrontUrl),
      adminUrl: trusted(result.adminUrl),
      shopDomain: result.shopDomain ?? null,
      unsentChanges: null,
    });
  }

  if (listing.kind === "checking") {
    return {
      ...base,
      kind: "checking",
      label: "Checking Shopify status…",
      detail: "Loading store listing information.",
      tone: "neutral",
    };
  }
  if (listing.kind === "unavailable") {
    return {
      ...base,
      kind: "unavailable",
      label: "Shopify status unavailable",
      detail: "We could not load Shopify status for this product.",
      tone: "warning",
      retry: true,
    };
  }

  const note = listing.refreshFailed
    ? "Couldn’t refresh Shopify status — showing the last known state."
    : listing.refreshing
      ? "Refreshing Shopify status…"
      : null;
  const withNote = { ...base, note, retry: listing.refreshFailed };

  if (listing.kind === "not-published") {
    return {
      ...withNote,
      kind: "not-on-shopify",
      label: "Not on Shopify",
      detail: "This draft is saved in DropPilot and has not been sent to a store.",
      tone: "neutral",
    };
  }

  const unsent = draftNewerThanSync(input.draftUpdatedAt, listing.listing?.lastSyncedAt);
  if (unsent === true) {
    return {
      ...withNote,
      unsentChanges: true,
      kind: "changes-not-sent",
      label: "Changes not sent to Shopify",
      detail:
        "Your latest saved changes are in DropPilot but have not been sent to Shopify.",
      tone: "warning",
    };
  }
  return visibilityState(listing.onlineStorePublished, { ...withNote, unsentChanges: unsent });
}

export function deriveSaveState(input: Pick<EditorLifecycleInput, "dirty" | "saveState" | "conflict">): SaveStateView {
  // Order preserved from the previous indicator: an in-flight request wins
  // over dirty, and a failure wins over a stale "saved".
  if (input.saveState === "saving") {
    return { kind: "saving", label: "Saving…", tone: "neutral", retry: false };
  }
  if (input.saveState === "error") {
    return { kind: "save-error", label: "Couldn’t save", tone: "danger", retry: true };
  }
  if (input.saveState === "conflict" || input.conflict) {
    return {
      kind: "conflict",
      label: "Saving paused — someone else saved this product",
      tone: "danger",
      retry: false,
    };
  }
  if (input.dirty) {
    return { kind: "unsaved", label: "Unsaved changes", tone: "warning", retry: false };
  }
  if (input.saveState === "saved") {
    return { kind: "saved", label: "Saved in DropPilot", tone: "success", retry: false };
  }
  return { kind: "clean", label: "Saved in DropPilot", tone: "neutral", retry: false };
}

function reviewLabel(issueCount: number): string {
  return issueCount > 0
    ? `Review ${issueCount} item${issueCount === 1 ? "" : "s"}`
    : "Review & publish";
}

/**
 * The single primary action for the header and the mobile bar. Header
 * controls navigate; only the Review & publish panel publishes.
 */
export function deriveNextAction(
  input: Pick<EditorLifecycleInput, "conflict" | "publishPending" | "dirty" | "issueCount">,
  shopify: ShopifyState,
): NextAction {
  if (input.conflict) return { kind: "resolve-conflict", label: "Resolve conflict" };
  if (input.publishPending) return { kind: "publishing", label: "Publishing…" };
  if (shopify.kind === "publish-failed") {
    return { kind: "retry-publish", label: "Try publishing again" };
  }
  if (shopify.hasSyncedListing) {
    if (input.dirty || shopify.unsentChanges === true) {
      return { kind: "update-shopify", label: "Update Shopify" };
    }
    return { kind: "view-product", label: "View product" };
  }
  if (input.issueCount > 0) {
    return { kind: "review-items", label: reviewLabel(input.issueCount) };
  }
  return { kind: "review-publish", label: "Review & publish" };
}

export function deriveEditorLifecycle(input: EditorLifecycleInput): EditorLifecycle {
  const shopify = deriveShopifyState(input);
  return {
    shopify,
    save: deriveSaveState(input),
    next: deriveNextAction(input, shopify),
  };
}
