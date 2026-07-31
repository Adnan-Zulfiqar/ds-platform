import type { ComponentType, ReactNode } from "react";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  /** Primary action — "Add your first product", "Connect a store". */
  action?: ReactNode;
  className?: string;
}

/**
 * Shown when a collection is legitimately empty.
 *
 * **An empty state is not an error state.** Nothing has gone wrong: the user
 * simply has no data yet. It should therefore explain the next useful step
 * rather than apologise, which is why `action` is a first-class prop — an empty
 * state without a route forward is a dead end.
 *
 * Distinct from `ErrorState` (something failed) and `Skeleton` (still loading).
 * Conflating the three is how a user ends up seeing "no products" when the
 * request actually failed.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed p-8 text-center sm:p-12",
        className,
      )}
    >
      {Icon && (
        <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-muted">
          {/* Decorative: the heading carries the meaning. */}
          <Icon className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
        </div>
      )}

      <h3 className="text-base font-semibold">{title}</h3>

      {description && (
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      )}

      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
