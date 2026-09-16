"use client";

import { Search, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { SORT_OPTIONS, type CatalogueQueryState, type SortValue } from "./catalogue-query";

interface CatalogueToolbarProps {
  state: CatalogueQueryState;
  /** What is being searched — "drafts" or "products" — for labels and copy. */
  noun: string;
  /** Total matching rows from the page metadata; `undefined` while unknown. */
  totalItems: number | undefined;
  /** True while a request for the current state is in flight. */
  busy: boolean;
}

const SEARCH_DEBOUNCE_MS = 300;

/**
 * Search and sort for a catalogue list.
 *
 * The search box is a real `form role="search"`: Enter submits at once,
 * typing submits after a short pause, and every submit *replaces* the
 * current history entry so a search does not leave one Back stop per
 * keystroke. The sort control pushes, because switching order is a step a
 * merchant may want to undo with Back. Both write to the URL, never to
 * component state alone, so the list and the address bar cannot disagree.
 */
export function CatalogueToolbar({ state, noun, totalItems, busy }: CatalogueToolbarProps) {
  const searchId = useId();
  const sortId = useId();
  const [draft, setDraft] = useState(state.query.q);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Back/forward or a shared link changes the URL under us; mirror it.
  useEffect(() => {
    setDraft(state.query.q);
  }, [state.query.q]);

  useEffect(() => {
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, []);

  const commit = (value: string) => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = null;
    const trimmed = value.trim();
    if (trimmed !== state.query.q) state.update({ q: trimmed }, { replace: true });
  };

  const onChange = (value: string) => {
    setDraft(value);
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => commit(value), SEARCH_DEBOUNCE_MS);
  };

  const summary =
    totalItems === undefined
      ? null
      : state.query.q
        ? `${totalItems.toLocaleString()} ${totalItems === 1 ? "result" : "results"} for “${state.query.q}”`
        : `${totalItems.toLocaleString()} ${totalItems === 1 ? noun.replace(/s$/, "") : noun}`;

  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between" data-testid="catalogue-toolbar">
      <form
        role="search"
        className="flex w-full max-w-md items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          commit(draft);
        }}
      >
        <div className="relative flex-1">
          <Label htmlFor={searchId} className="sr-only">
            Search {noun}
          </Label>
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            id={searchId}
            type="search"
            value={draft}
            onChange={(event) => onChange(event.target.value)}
            placeholder={`Search ${noun} by title, supplier or ID`}
            autoComplete="off"
            className="pl-9 pr-9"
            data-testid="catalogue-search"
          />
          {draft && (
            <button
              type="button"
              onClick={() => {
                setDraft("");
                commit("");
              }}
              aria-label="Clear search"
              className="absolute right-2 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>
        <Button type="submit" variant="outline" className="shrink-0">
          Search
        </Button>
      </form>

      <div className="flex items-end gap-3">
        <p
          className="hidden text-sm text-muted-foreground sm:block"
          role="status"
          aria-live="polite"
          data-testid="catalogue-summary"
        >
          {busy ? "Updating…" : summary}
        </p>
        <div>
          <Label htmlFor={sortId} className="sr-only">
            Sort by
          </Label>
          <select
            id={sortId}
            value={state.query.sort}
            onChange={(event) => state.update({ sort: event.target.value as SortValue })}
            className="h-9 rounded-md border border-input bg-transparent px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            data-testid="catalogue-sort"
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
