"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ExternalLink, Loader2, RefreshCw } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { draftKeys } from "@/services/drafts";
import { sendEbayPriceQuantity } from "@/services/integrations";
import { useStores } from "@/services/stores";
import type { StoreListing } from "@/types/api";

/**
 * EBAY-C4: this product's eBay listings — status, link, and "send price and
 * stock now". Price and stock are also sent automatically after an edit or a
 * supplier refresh; this button is for when the merchant wants it now, or
 * after fixing what an earlier send reported.
 */

/** eBay listing URLs are built by the server from fixed hosts; accept only those. */
function isEbayListingUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && /(^|\.)ebay\.(com|co\.uk|de|com\.au|ca|fr|it|es)$/.test(parsed.hostname);
  } catch {
    return false;
  }
}

/** `listings` comes from the page's own listings query — one request, not two. */
export function EbayListingsPanel({
  productId,
  listings: allListings,
}: {
  productId: string;
  listings: StoreListing[];
}) {
  const { hasRole } = useAuth();
  const canManage = hasRole("owner") || hasRole("admin");
  const queryClient = useQueryClient();
  const storesQuery = useStores({ size: 50 });
  const send = useMutation({
    mutationFn: () => sendEbayPriceQuantity(productId),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: draftKeys.listings(productId) });
    },
  });

  const ebayStores = new Map(
    (storesQuery.data?.items ?? [])
      .filter((store) => store.platform === "ebay")
      .map((store) => [store.id, store.name]),
  );
  const listings = allListings.filter((row) => ebayStores.has(row.storeId));
  if (listings.length === 0) return null;

  return (
    <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="eBay listings" data-testid="ebay-listings-panel">
      <h2 className="text-sm font-semibold">On eBay</h2>
      <ul className="space-y-3">
        {listings.map((listing) => (
          <li key={listing.id} className="space-y-1 text-sm" data-testid="ebay-listing-row">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{ebayStores.get(listing.storeId)}</span>
              <span className="text-muted-foreground">
                {listing.status === "error" ? "Needs attention" : `Last sent ${formatDateTime(listing.lastSyncedAt)}`}
              </span>
              {isEbayListingUrl(listing.storefrontUrl) ? (
                <a
                  href={listing.storefrontUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center underline-offset-4 hover:underline"
                >
                  View on eBay
                  <ExternalLink className="ml-1 h-3.5 w-3.5" aria-hidden="true" />
                </a>
              ) : null}
            </div>
            {listing.status === "error" && listing.lastError ? (
              <p className="text-muted-foreground" data-testid="ebay-listing-error">
                {listing.lastError}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {send.isError ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            {send.error instanceof ApiError ? send.error.message : "Could not reach eBay. Try again."}
          </AlertDescription>
        </Alert>
      ) : null}
      {send.isSuccess ? (
        <p className="text-sm text-muted-foreground" role="status" data-testid="ebay-send-result">
          {send.data.message}
        </p>
      ) : null}
      {canManage ? (
        <Button variant="outline" size="sm" onClick={() => send.mutate()} disabled={send.isPending}>
          {send.isPending ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          ) : (
            <RefreshCw className="mr-1.5 h-4 w-4" aria-hidden="true" />
          )}
          Send price and stock to eBay now
        </Button>
      ) : null}
    </section>
  );
}
