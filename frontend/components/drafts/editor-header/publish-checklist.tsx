"use client";

import { X } from "lucide-react";
import { useEffect } from "react";

import type { EditorTab } from "@/components/drafts/editor-header/product-editor-tabs";
import type { ReadinessSummary } from "@/components/drafts/editor-header/readiness";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useIsLgUp } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";
import type { SeoScore, StoreListing } from "@/types/api";
import type { Store } from "@/services/stores";
import { deriveStoreStatusLabel } from "@/components/drafts/editor-header/store-status-label";

interface PublishChecklistProps {
  readiness: ReadinessSummary;
  seoScore?: SeoScore | null;
  listing: StoreListing | null;
  shopifyStores: Store[];
  storesPending: boolean;
  storesError: boolean;
  open: boolean;
  onClose: () => void;
  onOpenTab: (tab: EditorTab) => void;
  variant?: "aside" | "sheet";
}

/**
 * Checklist chrome over client-side readiness hints.
 *
 * Does not claim Required/Recommended — the API does not classify findings.
 * Publishing still runs its own channel checks. The mobile sheet uses the
 * shared Radix Sheet primitive so Escape, focus trap, and focus return work.
 * Crossing Tailwind `lg` closes the sheet (not merely CSS-hides it) so the
 * portalled overlay cannot leave the desktop page inert.
 */
export function PublishChecklist({
  readiness,
  seoScore,
  listing,
  shopifyStores,
  storesPending,
  storesError,
  open,
  onClose,
  onOpenTab,
  variant = "aside",
}: PublishChecklistProps) {
  const isLgUp = useIsLgUp();
  const items = readiness.items;
  const hasItems = items.length > 0;
  const storeGuidance = deriveStoreStatusLabel({
    storesPending,
    storesError,
    shopifyStores,
    listing,
  });

  useEffect(() => {
    if (variant !== "sheet") return;
    if (!open) return;
    if (!isLgUp) return;
    onClose();
  }, [variant, open, isLgUp, onClose]);

  const body = (
    <div className="space-y-4" data-testid="publish-checklist">
      {hasItems ? (
        <section>
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Items to review
          </h4>
          <ul className="mt-2 space-y-2">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="w-full rounded-[10px] border border-border/80 bg-muted/20 px-3 py-2 text-left text-sm hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => onOpenTab(item.tab)}
                >
                  <span className="font-medium text-foreground">{item.message}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {item.effect}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {seoScore ? (
        <p className="text-sm text-muted-foreground">
          Search details {seoScore.score}/100
          {seoScore.status ? ` · ${seoScore.status}` : ""}
        </p>
      ) : null}

      <p className="text-sm text-muted-foreground" data-testid="store-status-guidance">
        {listing
          ? `Store listing: ${listing.status === "synced" ? "connected" : listing.status}`
          : storeGuidance}
      </p>
    </div>
  );

  const summary = hasItems
    ? `Review ${items.length} item${items.length === 1 ? "" : "s"} before you publish. Channel checks still run when you publish.`
    : "No title, description or image suggestions. Channel checks still run when you publish.";

  if (variant === "sheet") {
    return (
      <Sheet
        open={open}
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
      >
        <SheetContent
          side="bottom"
          hideCloseButton
          id="publish-checklist-sheet"
          aria-modal="true"
          className="max-h-[80vh] gap-0 overflow-y-auto rounded-t-[12px] p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
          data-testid="publish-checklist-sheet"
          onOpenAutoFocus={(event) => {
            // Land on Close — a predictable, labelled control — rather than the
            // first checklist row, which may navigate away on activation.
            const close = (event.currentTarget as HTMLElement).querySelector<HTMLElement>(
              '[data-testid="publish-checklist-close"]',
            );
            if (close) {
              event.preventDefault();
              close.focus();
            }
          }}
          onCloseAutoFocus={(event) => {
            // Controlled open (trigger lives in the header) — restore focus to
            // the exact control that opened the sheet.
            event.preventDefault();
            document
              .querySelector<HTMLElement>('[data-testid="things-to-fix-trigger"]')
              ?.focus();
          }}
        >
          <SheetHeader className="space-y-1 pr-12 text-left">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <SheetTitle>Before you publish</SheetTitle>
                <SheetDescription className="mt-1">{summary}</SheetDescription>
              </div>
              <SheetClose asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-3 top-3 h-11 w-11"
                  aria-label="Close checklist"
                  data-testid="publish-checklist-close"
                >
                  <X className="h-4 w-4" />
                </Button>
              </SheetClose>
            </div>
          </SheetHeader>
          <div className="mt-4">{body}</div>
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <aside
      className={cn("hidden space-y-4 lg:block xl:sticky xl:top-28 xl:self-start")}
      data-testid="publish-checklist-aside"
    >
      <div className="rounded-[10px] border border-border/80 bg-card p-4">
        <div className="mb-4">
          <h3 className="text-sm font-semibold">Before you publish</h3>
          <p className="mt-1 text-sm text-muted-foreground">{summary}</p>
        </div>
        {body}
      </div>
    </aside>
  );
}
