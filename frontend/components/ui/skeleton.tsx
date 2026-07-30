import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

/**
 * Loading placeholder.
 *
 * Preferred over a spinner for content that has a known shape: it preserves
 * layout, so the page does not jump when data arrives. `aria-hidden` keeps the
 * decorative blocks out of the accessibility tree — the surrounding region
 * should carry `aria-busy` instead so the loading state is announced once
 * rather than as a dozen meaningless nodes.
 */
function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded-md bg-muted", className)}
      {...props}
    />
  );
}

export { Skeleton };
