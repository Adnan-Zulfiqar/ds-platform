"use client";

import { AlertTriangle, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  title?: string;
  description?: string;
  /** Correlation id from the API response. Shown so support can trace it. */
  requestId?: string | null;
  onRetry?: () => void;
  className?: string;
}

/**
 * Shown when something failed.
 *
 * Two deliberate choices:
 *
 * * **`role="alert"`** — a failure that appears silently is one the user may
 *   not notice, especially after an action they believed had succeeded.
 * * **The request id is surfaced, not hidden.** It maps directly to the
 *   server-side log line, which turns most support conversations into a single
 *   query. Hiding it to keep the UI tidy costs far more than it saves.
 *
 * The description stays generic by default. Server error messages can contain
 * internal detail, so the API returns a safe message and this renders it as
 * given rather than elaborating.
 */
export function ErrorState({
  title = "Something went wrong",
  description = "We could not load this content. Please try again.",
  requestId,
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-destructive/30 bg-destructive/5 p-8 text-center sm:p-12",
        className,
      )}
    >
      <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="h-5 w-5 text-destructive" aria-hidden="true" />
      </div>

      <h3 className="text-base font-semibold">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>

      {requestId && (
        <p className="mt-3 text-xs text-muted-foreground">
          Reference: <code className="font-mono">{requestId}</code>
        </p>
      )}

      {onRetry && (
        <Button variant="outline" size="sm" className="mt-5" onClick={onRetry}>
          <RotateCw className="h-4 w-4" />
          Try again
        </Button>
      )}
    </div>
  );
}
