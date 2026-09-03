import { AlertCircle, Check, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type SaveState = "idle" | "saving" | "saved" | "error" | "conflict";

interface SaveStateIndicatorProps {
  dirty: boolean;
  saveState: SaveState;
  /**
   * When the product already has a synced store listing, successful saves are
   * described as draft changes that have not yet updated the live listing.
   * Only set when the server listing status confirms a live channel record.
   */
  isLiveOnStore?: boolean;
  onRetry?: () => void;
  className?: string;
}

export function SaveStateIndicator({
  dirty,
  saveState,
  isLiveOnStore = false,
  onRetry,
  className,
}: SaveStateIndicatorProps) {
  let label = isLiveOnStore
    ? "Changes saved as a draft — your live product has not changed"
    : "Draft saved — not live";
  let tone: "muted" | "warn" | "error" | "ok" = "muted";
  let Icon: typeof Check | null = Check;
  let showRetry = false;

  // In-flight request wins over dirty — derived from the real save lifecycle.
  if (saveState === "saving") {
    label = "Saving…";
    tone = "muted";
    Icon = Loader2;
  } else if (saveState === "error") {
    label = "Couldn’t save";
    tone = "error";
    Icon = AlertCircle;
    showRetry = Boolean(onRetry);
  } else if (saveState === "conflict") {
    label = "Someone else saved this product";
    tone = "error";
    Icon = AlertCircle;
  } else if (dirty) {
    label = "Unsaved changes";
    tone = "warn";
    Icon = null;
  } else if (saveState === "saved") {
    label = isLiveOnStore
      ? "Changes saved as a draft — your live product has not changed"
      : "Draft saved — not live";
    tone = "ok";
    Icon = Check;
  } else {
    label = isLiveOnStore
      ? "Changes saved as a draft — your live product has not changed"
      : "Draft saved — not live";
    tone = "muted";
    Icon = Check;
  }

  return (
    <span
      className={cn(
        "inline-flex max-w-full flex-wrap items-center gap-1.5 text-xs font-medium",
        tone === "warn" && "text-amber-700 dark:text-amber-400",
        tone === "error" && "text-destructive",
        tone === "ok" && "text-emerald-700 dark:text-emerald-400",
        tone === "muted" && "text-muted-foreground",
        className,
      )}
      data-testid="draft-save-state"
      aria-live="polite"
      aria-atomic="true"
    >
      {Icon ? (
        <Icon
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            saveState === "saving" && "animate-spin motion-reduce:animate-none",
          )}
          aria-hidden="true"
        />
      ) : null}
      <span className="min-w-0">{label}</span>
      {showRetry ? (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto px-1 py-0 text-xs"
          onClick={onRetry}
          data-testid="draft-save-retry"
        >
          Try again
        </Button>
      ) : null}
    </span>
  );
}
