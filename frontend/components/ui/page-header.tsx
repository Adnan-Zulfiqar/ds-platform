import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  description?: string;
  /** Page-level actions, right-aligned on desktop. */
  actions?: ReactNode;
  className?: string;
}

/**
 * Standard page heading.
 *
 * Every route uses this, which is what makes the application feel like one
 * product rather than a set of screens built at different times. It also fixes
 * the heading level: exactly one `h1` per page, which is what a screen reader
 * user relies on to orient themselves after navigating.
 *
 * Actions wrap below the title on narrow viewports rather than shrinking the
 * heading — a truncated page title is worse than a taller header.
 */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between",
        className,
      )}
    >
      <div className="min-w-0 space-y-1">
        <h1 className="truncate text-2xl font-semibold tracking-tight">{title}</h1>
        {description && (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>

      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
