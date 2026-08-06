import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

interface ProductEditorBreadcrumbProps {
  className?: string;
}

export function ProductEditorBreadcrumb({
  className,
}: ProductEditorBreadcrumbProps) {
  return (
    <nav
      aria-label="Breadcrumb"
      className={cn("min-w-0", className)}
      data-testid="product-editor-breadcrumb"
    >
      <ol className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
        <li>
          <Link
            href="/products"
            className="rounded-sm transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Products
          </Link>
        </li>
        <li aria-hidden="true" className="text-border">
          <ChevronRight className="h-3.5 w-3.5" />
        </li>
        <li>
          <Link
            href="/drafts"
            className="rounded-sm transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Drafts
          </Link>
        </li>
        <li aria-hidden="true" className="text-border">
          <ChevronRight className="h-3.5 w-3.5" />
        </li>
        <li className="truncate font-medium text-foreground" aria-current="page">
          Edit Product
        </li>
      </ol>
    </nav>
  );
}
