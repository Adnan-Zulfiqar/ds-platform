"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface HomeSectionProps {
  id: string;
  title: string;
  /** Optional trailing link, e.g. "View all". */
  link?: { href: string; label: string };
  children: ReactNode;
  className?: string;
}

/**
 * One block of the Home page: a labelled landmark with its own heading.
 *
 * Every block is a `section` named by its `h2`, so a screen-reader user can
 * jump between "Needs attention", "Continue working" and so on, and the
 * tests can address a block by name rather than by position.
 */
export function HomeSection({ id, title, link, children, className }: HomeSectionProps) {
  const headingId = `${id}-heading`;
  return (
    <section aria-labelledby={headingId} className={cn("space-y-3", className)} data-testid={id}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 id={headingId} className="text-base font-semibold">
          {title}
        </h2>
        {link && (
          <Link
            href={link.href}
            className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            {link.label}
          </Link>
        )}
      </div>
      {children}
    </section>
  );
}

interface BlockStateProps {
  /** Rows of skeleton to draw while loading — sized like the final content. */
  rows?: number;
  rowHeight?: string;
}

/** Placeholder that occupies the same space the loaded block will. */
export function BlockSkeleton({ rows = 3, rowHeight = "h-12" }: BlockStateProps) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className={cn("w-full rounded-md", rowHeight)} />
      ))}
    </div>
  );
}

interface BlockErrorProps {
  what: string;
  onRetry?: () => void;
}

/**
 * A block that could not load. Compact and local: one failed request must not
 * take the rest of Home with it, and the merchant is told which part failed
 * rather than shown a page-level error.
 */
export function BlockError({ what, onRetry }: BlockErrorProps) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm"
    >
      <span>Couldn’t load {what}.</span>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          Retry
        </Button>
      )}
    </div>
  );
}
