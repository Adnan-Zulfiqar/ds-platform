"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

import type { EditorTab } from "@/components/drafts/editor-header/product-editor-tabs";
import type { ReadinessSummary } from "@/components/drafts/editor-header/readiness";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SeoScore, StoreListing } from "@/types/api";

interface PublishChecklistProps {
  readiness: ReadinessSummary;
  seoScore?: SeoScore | null;
  listing: StoreListing | null;
  open: boolean;
  onClose: () => void;
  onOpenTab: (tab: EditorTab) => void;
  variant?: "aside" | "sheet";
}

export function PublishChecklist({
  readiness,
  seoScore,
  listing,
  open,
  onClose,
  onOpenTab,
  variant = "aside",
}: PublishChecklistProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const required = readiness.items.filter((i) => i.severity === "required");
  const recommended = readiness.items.filter((i) => i.severity === "recommended");
  const ready = required.length === 0;

  useEffect(() => {
    if (variant !== "sheet" || !open) return;
    closeRef.current?.focus();
  }, [variant, open]);

  const body = (
    <div className="space-y-4" data-testid="publish-checklist">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Before you publish</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            {ready
              ? "Required checks look complete. Channel checks still run when you publish."
              : `Fix ${required.length} thing${required.length === 1 ? "" : "s"} to publish.`}
          </p>
        </div>
        {variant === "sheet" ? (
          <Button
            ref={closeRef}
            type="button"
            variant="ghost"
            size="icon"
            className="h-11 w-11"
            onClick={onClose}
            aria-label="Close checklist"
          >
            <X className="h-4 w-4" />
          </Button>
        ) : null}
      </div>

      {required.length > 0 ? (
        <section>
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Required
          </h4>
          <ul className="mt-2 space-y-2">
            {required.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="w-full rounded-[10px] border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-left text-sm hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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

      {recommended.length > 0 ? (
        <section>
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Recommended
          </h4>
          <ul className="mt-2 space-y-2">
            {recommended.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="w-full rounded-[10px] border border-border/80 px-3 py-2 text-left text-sm hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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

      {listing ? (
        <p className="text-sm text-muted-foreground">
          Store listing: {listing.status === "synced" ? "connected" : listing.status}
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">
          Connect Shopify to publish.
        </p>
      )}
    </div>
  );

  if (variant === "sheet") {
    if (!open) return null;
    return (
      <div
        className="fixed inset-0 z-40 lg:hidden"
        data-testid="publish-checklist-sheet"
      >
        <button
          type="button"
          className="absolute inset-0 bg-black/40"
          aria-label="Dismiss checklist overlay"
          onClick={onClose}
        />
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="checklist-title"
          className="absolute inset-x-0 bottom-0 max-h-[80vh] overflow-y-auto rounded-t-[12px] border bg-background p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-lg"
        >
          <p id="checklist-title" className="sr-only">
            Before you publish
          </p>
          {body}
        </div>
      </div>
    );
  }

  // Desktop keeps the checklist visible; `open` only drives the mobile sheet.
  return (
    <aside
      className={cn(
        "hidden space-y-4 lg:block xl:sticky xl:top-28 xl:self-start",
      )}
      data-testid="publish-checklist-aside"
    >
      <div className="rounded-[10px] border border-border/80 bg-card p-4">{body}</div>
    </aside>
  );
}
