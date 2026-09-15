import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface PageContainerProps {
  children: ReactNode;
  className?: string;
}

/**
 * The one place a page's outer gutter and width are decided.
 *
 * Before UX-L2D-02 each page carried (or forgot) its own `p-4 sm:p-6`: Drafts,
 * Products, Import history and the editor rendered flush against the sidebar
 * and the viewport edge while every other page had a gutter. The protected
 * layout now wraps every page in this container, so a new route gets the
 * gutter without anyone remembering to add it, and no page can drift.
 *
 * Width is capped at the `2xl` screen size and centred: on a 1920px display
 * a table that stretches edge to edge is harder to scan than one that sits in
 * a bounded column, and nothing in the product benefits from the extra span.
 * Pages own their internal vertical rhythm (`space-y-*`); the container only
 * supplies it for children that have none.
 */
export function PageContainer({ children, className }: PageContainerProps) {
  return (
    <div
      data-testid="page-container"
      className={cn("mx-auto w-full max-w-screen-2xl space-y-6 p-4 sm:p-6", className)}
    >
      {children}
    </div>
  );
}
