"use client";

import { AlertTriangle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Shown when a save was rejected as stale (409).
 *
 * A conflict is a state to resolve, not a message to dismiss. Two things are
 * true at once — the merchant has unsaved edits, and the server has a newer
 * version — and only the merchant can decide which matters. So there is no
 * automatic resolution, no silent retry, and no "saved" badge.
 *
 * **"Keep my changes" means exactly that: keep editing, here, locally.** It
 * does not refresh the concurrency token, does not save, and does not
 * overwrite the newer version — a button that quietly re-armed the token would
 * turn "I want to look at my work" into "discard someone else's". Saving is
 * still refused until the newer version is loaded, and the copy says so rather
 * than implying otherwise.
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
            Nothing has been saved. <strong>Reload</strong> takes the newer
            version and discards your edits. <strong>Keep my changes</strong>
            leaves them on screen so you can copy them out — but saving stays
            blocked until you reload, because the version you started from no
            longer exists.
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
          data-testid="keep-my-changes"
        >
          Keep my changes
        </Button>
      </div>
    </div>
  );
}
