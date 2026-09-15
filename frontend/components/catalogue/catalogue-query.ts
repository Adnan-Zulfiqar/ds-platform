"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import type { ListQuery } from "@/types/api";

/**
 * The catalogue list state — search, sort, page — and its URL form.
 *
 * The URL is the state (UX-L2D-04): `/drafts?q=lamp&sort=title:asc&page=2`
 * survives a refresh, travels in a shared link, and steps back and forward
 * with the browser. Everything here is validated against what the backend
 * actually accepts, so a hand-edited or stale URL falls back to defaults
 * rather than producing a 422 or an empty page.
 *
 * Sort options are the backend's `sortable_fields` for products, spelled in
 * the wire names it reads (`sort_by`/`sort_dir` via `toListParams`). Search
 * is the shared `q` parameter, matched server-side against title, external
 * id and supplier name. There is no lifecycle, AI-status or readiness filter
 * because the API has no such parameter; a client-side filter over one page
 * would silently hide matching rows on other pages, so none is offered.
 */

export const SORT_OPTIONS = [
  { value: "updated_at:desc", label: "Recently updated" },
  { value: "created_at:desc", label: "Newest first" },
  { value: "created_at:asc", label: "Oldest first" },
  { value: "title:asc", label: "Title A–Z" },
  { value: "title:desc", label: "Title Z–A" },
  { value: "cost_price_min:asc", label: "Supplier price, low to high" },
  { value: "cost_price_min:desc", label: "Supplier price, high to low" },
  { value: "stock_quantity:desc", label: "Stock, high to low" },
] as const;

export type SortValue = (typeof SORT_OPTIONS)[number]["value"];

export const DEFAULT_SORT: SortValue = "updated_at:desc";
export const PAGE_SIZE = 25;
const MAX_SEARCH_LENGTH = 255;

export interface CatalogueQuery {
  q: string;
  sort: SortValue;
  page: number;
}

export const DEFAULT_QUERY: CatalogueQuery = { q: "", sort: DEFAULT_SORT, page: 1 };

function isSortValue(value: string | null): value is SortValue {
  return SORT_OPTIONS.some((option) => option.value === value);
}

/** Read the list state from search params; anything invalid becomes the default. */
export function parseCatalogueQuery(params: URLSearchParams): CatalogueQuery {
  const q = (params.get("q") ?? "").trim().slice(0, MAX_SEARCH_LENGTH);
  const sortParam = params.get("sort");
  const sort = isSortValue(sortParam) ? sortParam : DEFAULT_SORT;
  const pageParam = Number.parseInt(params.get("page") ?? "", 10);
  const page = Number.isInteger(pageParam) && pageParam >= 1 ? pageParam : 1;
  return { q, sort, page };
}

/** The URL form; defaults are omitted so an untouched list has a clean URL. */
export function toSearchParams(query: CatalogueQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.sort !== DEFAULT_SORT) params.set("sort", query.sort);
  if (query.page > 1) params.set("page", String(query.page));
  return params;
}

/** The API form, in the names the services translate to the wire. */
export function toListQuery(query: CatalogueQuery): ListQuery {
  const [sortBy, sortDir] = query.sort.split(":") as [string, "asc" | "desc"];
  return {
    page: query.page,
    size: PAGE_SIZE,
    sortBy,
    sortDir,
    ...(query.q ? { q: query.q } : {}),
  };
}

export interface CatalogueQueryState {
  query: CatalogueQuery;
  /**
   * Update part of the state. `replace` rewrites the current history entry
   * — used while typing a search so each keystroke is not a Back stop;
   * everything else pushes, so sort and page changes step back naturally.
   */
  update: (patch: Partial<CatalogueQuery>, options?: { replace?: boolean }) => void;
}

export function useCatalogueQuery(): CatalogueQueryState {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const query = useMemo(() => parseCatalogueQuery(searchParams), [searchParams]);

  const update = useCallback(
    (patch: Partial<CatalogueQuery>, options?: { replace?: boolean }) => {
      const next: CatalogueQuery = { ...query, ...patch };
      // A new search or sort starts from the first page unless a page was
      // asked for explicitly.
      if (!("page" in patch) && ("q" in patch || "sort" in patch)) next.page = 1;
      const params = toSearchParams(next).toString();
      const href = params ? `${pathname}?${params}` : pathname;
      if (href === (searchParams.size ? `${pathname}?${searchParams.toString()}` : pathname)) return;
      // `scroll: false`: changing a page or a sort should not throw the
      // viewport to the top of the document.
      if (options?.replace) router.replace(href, { scroll: false });
      else router.push(href, { scroll: false });
    },
    [pathname, query, router, searchParams],
  );

  return { query, update };
}
