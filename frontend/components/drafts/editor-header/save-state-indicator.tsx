"use client";

import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { SaveStateView } from "@/lib/editor-lifecycle";
import { cn } from "@/lib/utils";

/** The editor's raw save phase; the lifecycle layer turns it into words. */
export type SaveState = "idle" | "saving" | "saved" | "error" | "conflict";

interface SaveStateIndicatorProps {
  save: SaveStateView;
  onRetry?: () => void;
  className?: string;
}

/**
 * Where the merchant's edits are — in the browser, in flight, or in
 * DropPilot. Never where they are on Shopify: that is the listing badge's
 * job, and the two sit side by side so neither has to hedge the other.
 *
 * The visible text changes with every phase. The announcement does not:
 * "Saving…" is skipped so an autosave cycle reads as one update ("Saved in
 * DropPilot") rather than two, and the live region sits outside the test id
 * so assertions see only what a sighted merchant sees.
 */
export function SaveStateIndicator({ save, onRetry, className }: SaveStateIndicatorProps) {
  const [announced, setAnnounced] = useState(save.label);
  useEffect(() => {
    if (save.kind !== "saving") setAnnounced(save.label);
  }, [save.kind, save.label]);

  const Icon =
    save.kind === "saving"
      ? Loader2
      : save.kind === "save-error" || save.kind === "conflict"
        ? AlertCircle
        : save.kind === "unsaved"
          ? null
          : Check;

  return (
    <>
      <span
        className={cn(
          "inline-flex max-w-full items-center gap-1.5 text-xs font-medium",
          save.tone === "warning" && "text-amber-700 dark:text-amber-400",
          save.tone === "danger" && "text-destructive",
          save.tone === "success" && "text-emerald-700 dark:text-emerald-400",
          save.tone === "neutral" && "text-muted-foreground",
          className,
        )}
        data-testid="draft-save-state"
        data-kind={save.kind}
      >
        {Icon ? (
          <Icon
            className={cn(
              "h-3.5 w-3.5 shrink-0",
              save.kind === "saving" && "animate-spin motion-reduce:animate-none",
            )}
            aria-hidden="true"
          />
        ) : null}
        <span className="min-w-0">{save.label}</span>
        {save.retry && onRetry ? (
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
      <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announced}
      </span>
    </>
  );
}
