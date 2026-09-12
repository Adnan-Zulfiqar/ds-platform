"use client";

import { Loader2, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ProductLifecycleView } from "@/lib/product-lifecycle";

interface ShopifyListingStatusProps {
  lifecycle: ProductLifecycleView;
  onRetry?: () => void;
  retryInFlight?: boolean;
  className?: string;
}

/**
 * Shared retry, refresh note, and live-region announcements for Shopify listing status.
 */
export function ShopifyListingStatus({
  lifecycle,
  onRetry,
  retryInFlight = false,
  className,
}: ShopifyListingStatusProps) {
  const liveMessage = [
    lifecycle.badgeLabel,
    lifecycle.statusNoteMessage,
    lifecycle.supportingCopy,
  ]
    .filter(Boolean)
    .join(". ");

  const showRetry =
    lifecycle.showListingsRetry && typeof onRetry === "function";

  if (!lifecycle.statusNoteMessage && !showRetry) {
    return (
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {liveMessage}
      </div>
    );
  }

  return (
    <div className={className} data-testid="shopify-listing-status">
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {liveMessage}
      </div>
      {lifecycle.statusNoteMessage ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="shopify-status-note"
        >
          {lifecycle.statusNoteMessage}
        </p>
      ) : null}
      {showRetry ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="min-h-11 min-w-11"
          disabled={retryInFlight}
          onClick={onRetry}
          data-testid="shopify-status-retry"
        >
          {retryInFlight ? (
            <>
              <Loader2
                className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none"
                aria-hidden="true"
              />
              Checking…
            </>
          ) : (
            <>
              <RotateCw className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Try again
            </>
          )}
        </Button>
      ) : null}
    </div>
  );
}
