"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { PageMeta } from "@/types/api";

interface CataloguePaginationProps {
  meta: PageMeta;
  onPage: (page: number) => void;
  /** True while a page request is in flight; steps are disabled meanwhile. */
  busy: boolean;
  /** Singular/plural noun for the count, e.g. "draft"/"drafts". */
  noun: [singular: string, plural: string];
}

/**
 * Previous / next paging driven by the API's own `meta`.
 *
 * `hasPrevious`/`hasNext` come from the server, so the controls cannot offer
 * a page that does not exist, and the count is the server's `totalItems` —
 * the first page is never presented as the whole catalogue. Steps disable
 * while a page is loading so two quick clicks cannot skip one.
 */
export function CataloguePagination({ meta, onPage, busy, noun }: CataloguePaginationProps) {
  if (meta.totalItems === 0) return null;
  const first = (meta.page - 1) * meta.size + 1;
  const last = Math.min(meta.page * meta.size, meta.totalItems);

  return (
    <nav
      aria-label="Pagination"
      className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
      data-testid="catalogue-pagination"
    >
      <p className="text-sm text-muted-foreground" data-testid="pagination-summary">
        Showing {first.toLocaleString()}–{last.toLocaleString()} of {meta.totalItems.toLocaleString()}{" "}
        {meta.totalItems === 1 ? noun[0] : noun[1]}
        {meta.totalPages > 1 ? ` · page ${meta.page} of ${meta.totalPages}` : ""}
      </p>
      {meta.totalPages > 1 && (
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!meta.hasPrevious || busy}
            onClick={() => onPage(meta.page - 1)}
            aria-label="Previous page"
            data-testid="pagination-previous"
          >
            <ChevronLeft className="mr-1 h-4 w-4" aria-hidden="true" />
            Previous
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!meta.hasNext || busy}
            onClick={() => onPage(meta.page + 1)}
            aria-label="Next page"
            data-testid="pagination-next"
          >
            Next
            <ChevronRight className="ml-1 h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      )}
    </nav>
  );
}
