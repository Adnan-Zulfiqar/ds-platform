"use client";

import { Loader2, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ShopifyState, StateTone } from "@/lib/editor-lifecycle";
import { cn } from "@/lib/utils";

interface ShopifyStatusProps {
  state: ShopifyState;
  /** Re-request the listings. Offered only when the state says so. */
  onRetry?: () => void;
  retryInFlight?: boolean;
  className?: string;
}

const TONE_CLASS: Record<StateTone, string> = {
  neutral: "border-border bg-muted text-foreground",
  info: "border-sky-500/30 bg-sky-500/10 text-sky-900 dark:text-sky-200",
  success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
  warning: "border-amber-500/30 bg-amber-500/10 text-amber-900 dark:text-amber-200",
  danger: "border-destructive/30 bg-destructive/10 text-destructive",
};

/**
 * Where the product stands on Shopify, as one badge plus an optional note
 * and retry. Adapted from the reviewed historical `ShopifyListingStatus`
 * (UX-L2D-GATE-04): the note/retry/live-region trio is kept; the badge is
 * folded in so the header renders a single element for the state rather
 * than a badge here and a note somewhere else.
 *
 * The badge itself is the live region. Its text changes rarely and each
 * change is worth hearing — "Sending to Shopify…" to "Added to Shopify" is
 * the publish-progress announcement — unlike the save indicator, which
 * moves on every keystroke pause.
 */
export function ShopifyStatus({ state, onRetry, retryInFlight = false, className }: ShopifyStatusProps) {
  const showRetry = state.retry && typeof onRetry === "function";
  return (
    <span className={cn("inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1", className)}>
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium",
          TONE_CLASS[state.tone],
        )}
        data-testid="product-lifecycle"
        data-kind={state.kind}
        aria-live="polite"
        aria-atomic="true"
      >
        {state.kind === "publishing" || state.kind === "checking" ? (
          <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        ) : null}
        {state.label}
      </span>
      {state.note ? (
        <span className="text-xs text-muted-foreground" data-testid="shopify-status-note">
          {state.note}
        </span>
      ) : null}
      {showRetry ? (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto min-h-11 px-1 py-0 text-xs"
          disabled={retryInFlight}
          onClick={onRetry}
          data-testid="shopify-status-retry"
        >
          {retryInFlight ? (
            <>
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              Checking…
            </>
          ) : (
            <>
              <RotateCw className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              Try again
            </>
          )}
        </Button>
      ) : null}
    </span>
  );
}
