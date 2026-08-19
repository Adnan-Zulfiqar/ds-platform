"use client";

import { AlertTriangle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Shown when a save was rejected as stale (409).
 *
 * A conflict is a state to resolve, not a message to dismiss. Two things are
 * true at once — the merchant has unsaved edits, and the server has a newer
 * version — and only the merchant can decide which matters. So there is no
 * automatic resolution, no silent retry, and no "saved" badge: the banner
 * stays until an explicit choice is made.
 *
 * Reloading is destructive to local edits, which is why it says so, and why
 * the alternative is offered next to it rather than buried.
 */
export function ConflictBanner({
  message,
  onReload,
  onKeepEditing,
  reloading,
}: {
  message: string;
  onReload: () => void;
  onKeepEditing: () => void;
  reloading?: boolean;
}) {
  return (
    <div
      role="alert"
      data-testid="conflict-banner"
      className="space-y-3 rounded-lg border border-amber-500/50 bg-amber-500/10 p-4"
    >
      <div className="flex gap-3">
        <AlertTriangle
          className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400"
          aria-hidden="true"
        />
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium">This rule changed while you were editing</p>
          <p className="text-sm text-muted-foreground">{message}</p>
          <p className="text-sm text-muted-foreground">
            Nothing has been saved. Reload to take the newer version and lose
            your edits, or keep editing and save again to overwrite it
            deliberately.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          className="min-h-10"
          onClick={onReload}
          disabled={reloading}
        >
          {reloading && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
          )}
          Reload latest version
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="min-h-10"
          onClick={onKeepEditing}
        >
          Keep my changes
        </Button>
      </div>
    </div>
  );
}
