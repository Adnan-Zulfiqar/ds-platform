"use client";

import { useRef, type ReactNode } from "react";

import { useScrollEdges } from "@/hooks/use-scroll-edges";
import { cn } from "@/lib/utils";

interface NavScrollRegionProps {
  children: ReactNode;
  className?: string;
  /** The surface the fade dissolves into — the sidebar is a card, the drawer is not. */
  surface?: "card" | "background";
}

/**
 * The scrolling part of a navigation list, with fades on whichever edge is
 * hiding content.
 *
 * Shared by the desktop sidebar and the mobile drawer so both answer the same
 * UX-L2D-01 finding: below ~900px of viewport height the last items dropped
 * out of view with nothing to say so. The fades are purely visual —
 * `pointer-events-none`, hidden from assistive technology — and the region
 * remains an ordinary scroll container for keyboard and screen-reader users.
 * `data-scroll-bottom` exposes the measured state for tests.
 */
export function NavScrollRegion({
  children,
  className,
  surface = "card",
}: NavScrollRegionProps) {
  const from = surface === "card" ? "from-card" : "from-background";
  const ref = useRef<HTMLDivElement | null>(null);
  const edges = useScrollEdges(ref);

  return (
    <div className={cn("relative min-h-0 flex-1", className)}>
      <div
        ref={ref}
        className="h-full overflow-y-auto"
        data-testid="nav-scroll-region"
        data-scroll-top={edges.top ? "true" : "false"}
        data-scroll-bottom={edges.bottom ? "true" : "false"}
      >
        {children}
      </div>
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b to-transparent transition-opacity",
          from,
          edges.top ? "opacity-100" : "opacity-0",
        )}
      />
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t to-transparent transition-opacity",
          from,
          edges.bottom ? "opacity-100" : "opacity-0",
        )}
      />
    </div>
  );
}
