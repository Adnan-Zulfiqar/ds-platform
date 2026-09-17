"use client";

import { useEffect } from "react";

import { ErrorState } from "@/components/ui/error-state";

/**
 * Error boundary for authenticated routes.
 *
 * Scoped to this segment rather than the root, so a failure inside the content
 * area does not blank the shell — the user keeps their navigation and can move
 * somewhere else instead of being stranded on a full-page error.
 *
 * `reset` re-renders the segment, which recovers from a transient failure
 * without a full reload.
 */
export default function ProtectedError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Replace with the real error reporter once observability is wired up.
    // Logged in an effect rather than during render, because render may run
    // more than once for the same error.
    console.error("Unhandled error in protected route:", error);
  }, [error]);

  return (
    <div>
      <ErrorState
        title="Something went wrong"
        description="This page could not be displayed. You can try again, and if the problem persists please contact support."
        // Next.js withholds the message in production because it can contain
        // internal detail; the digest is the identifier support can trace.
        requestId={error.digest ?? null}
        onRetry={reset}
      />
    </div>
  );
}
