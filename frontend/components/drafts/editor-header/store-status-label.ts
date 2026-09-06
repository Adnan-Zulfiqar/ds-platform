import type { Store } from "@/services/stores";
import type { StoreListing } from "@/types/api";

/**
 * Header / checklist store guidance.
 *
 * Publication readiness remains authoritative on Review & publish. This copy
 * only reflects whether a usable Shopify store exists in workspace state —
 * never invents "connected" from a missing listing, and never claims
 * "Connect Shopify" while stores are still loading.
 */
export function deriveStoreStatusLabel(params: {
  storesPending: boolean;
  storesError: boolean;
  shopifyStores: Store[];
  listing: StoreListing | null | undefined;
}): string {
  const listing = params.listing ?? null;
  if (listing) {
    if (listing.status === "synced") {
      return listing.shopDomain || "Connected store";
    }
    if (listing.status === "error") {
      return "Store needs attention";
    }
    return "Connecting store…";
  }
  if (params.storesPending) {
    return "Choose a store in Review & publish";
  }
  if (params.storesError) {
    return "Store status unavailable";
  }
  const hasUsable = params.shopifyStores.some(
    (store) => store.status === "connected",
  );
  if (hasUsable) {
    return "Choose a store in Review & publish";
  }
  return "Connect Shopify to publish";
}
