"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  type KeyboardEvent,
} from "react";

import { useScrollEdges } from "@/hooks/use-scroll-edges";
import { cn } from "@/lib/utils";

export const EDITOR_TABS = [
  "overview",
  "description",
  "media",
  "variants",
  "pricing",
  "inventory",
  "shipping",
  "seo",
  "ai-studio",
  "publishing",
  "history",
] as const;

export type EditorTab = (typeof EDITOR_TABS)[number];

/** Plain-language labels for the command-bar navigation. */
export const EDITOR_TAB_LABEL: Record<EditorTab, string> = {
  overview: "Product details",
  description: "Description",
  media: "Images & video",
  variants: "Options & variants",
  pricing: "Price & profit",
  inventory: "Stock",
  shipping: "Shipping",
  seo: "Search & SEO",
  "ai-studio": "AI tools",
  publishing: "Review & publish",
  history: "History",
};

export type EditorNavGroupId = "product" | "selling" | "improve" | "publish";

export const EDITOR_NAV_GROUPS: {
  id: EditorNavGroupId;
  label: string;
  tabs: EditorTab[];
}[] = [
  {
    id: "product",
    label: "Product",
    tabs: ["overview", "description", "media"],
  },
  {
    id: "selling",
    label: "Selling",
    tabs: ["variants", "pricing", "inventory", "shipping"],
  },
  {
    id: "improve",
    label: "Improve",
    tabs: ["seo", "ai-studio"],
  },
  {
    id: "publish",
    label: "Publish",
    tabs: ["publishing"],
  },
];

/** History stays reachable from More actions; it is not a primary equal tab. */
export const EDITOR_SECONDARY_TABS: EditorTab[] = ["history"];

export function isEditorTab(value: string | null): value is EditorTab {
  return value !== null && (EDITOR_TABS as readonly string[]).includes(value);
}

interface TabIndicator {
  issues?: number;
  score?: number;
  blocked?: boolean;
}

interface ProductEditorTabsProps {
  activeTab: EditorTab;
  onChange: (tab: EditorTab) => void;
  indicators?: Partial<Record<EditorTab, TabIndicator>>;
  className?: string;
}

/**
 * The editor's section strip.
 *
 * Ten sections in four groups do not fit at 1024px, or at 1440px with the
 * sidebar open (UX-L2D-01 F-9), so the strip scrolls sideways — but a strip
 * that is merely `overflow-x: auto` ends exactly where the viewport does and
 * looks complete when it is not. Three things say otherwise: a fade on
 * whichever edge hides content, a chevron on that edge from `md` up (pointer
 * users; keyboard users already have Arrow/Home/End on the tablist, so the
 * chevrons are outside the tab order), and the selected tab is scrolled into
 * view whenever it changes — a deep link to `?tab=publishing` lands with
 * Review & publish visible rather than off-screen to the right.
 *
 * `data-scroll-left` / `data-scroll-right` expose the measured state for
 * tests. Replacing the strip with a dropdown was considered and rejected: it
 * would drop the tablist semantics every existing editor test relies on and
 * hide the group structure that makes ten sections legible.
 */
export function ProductEditorTabs({
  activeTab,
  onChange,
  indicators,
  className,
}: ProductEditorTabsProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const edges = useScrollEdges(listRef);
  const flatTabs = EDITOR_NAV_GROUPS.flatMap((group) => group.tabs);

  const focusTab = useCallback((index: number) => {
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]',
    );
    buttons?.[index]?.focus();
  }, []);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const reveal = () => {
      // `block: "nearest"` keeps this a horizontal adjustment: the header is
      // sticky, so there is no vertical distance to close.
      el.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.scrollIntoView({
        inline: "nearest",
        block: "nearest",
      });
    };
    reveal();
    // The first reveal can run before the web font has swapped in or before
    // the sidebar has settled, after which the tabs are wider or the strip
    // narrower and the selected tab is clipped again. Re-reveal whenever the
    // strip or any group changes size (the groups carry the content width;
    // the strip carries the viewport width) — a resize is a re-layout, and
    // showing the selected section after one is the expected outcome.
    const observer = new ResizeObserver(reveal);
    observer.observe(el);
    el.querySelectorAll<HTMLElement>(":scope > div").forEach((group) => observer.observe(group));
    return () => observer.disconnect();
  }, [activeTab]);

  const scrollBy = (direction: -1 | 1) => {
    const el = listRef.current;
    if (!el) return;
    el.scrollBy({ left: direction * Math.max(160, el.clientWidth * 0.6), behavior: "smooth" });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = flatTabs.indexOf(activeTab);
    if (current < 0) return;

    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      const next = (current + 1) % flatTabs.length;
      const nextTab = flatTabs[next];
      if (!nextTab) return;
      onChange(nextTab);
      focusTab(next);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      const prev = (current - 1 + flatTabs.length) % flatTabs.length;
      const prevTab = flatTabs[prev];
      if (!prevTab) return;
      onChange(prevTab);
      focusTab(prev);
    } else if (event.key === "Home") {
      event.preventDefault();
      const first = flatTabs[0];
      if (!first) return;
      onChange(first);
      focusTab(0);
    } else if (event.key === "End") {
      event.preventDefault();
      const last = flatTabs.length - 1;
      const lastTab = flatTabs[last];
      if (!lastTab) return;
      onChange(lastTab);
      focusTab(last);
    }
  };

  return (
    <div className={cn("relative", className)} data-testid="product-editor-tabs-region">
      <div
        ref={listRef}
        role="tablist"
        aria-label="Editor sections"
        onKeyDown={onKeyDown}
        className="flex gap-4 overflow-x-auto pb-px [scrollbar-width:thin] md:gap-5"
        data-testid="product-editor-tabs"
        data-scroll-left={edges.left ? "true" : "false"}
        data-scroll-right={edges.right ? "true" : "false"}
      >
        {EDITOR_NAV_GROUPS.map((group) => (
          <div
            key={group.id}
            className="flex shrink-0 flex-col gap-1"
            data-testid={`editor-nav-group-${group.id}`}
          >
            <p className="px-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {group.label}
            </p>
            <div className="flex gap-0.5">
              {group.tabs.map((id) => {
                const selected = activeTab === id;
                const indicator = indicators?.[id];
                let suffix = "";
                if (indicator?.blocked) suffix = " · Review";
                else if (
                  typeof indicator?.issues === "number" &&
                  indicator.issues > 0
                ) {
                  suffix = ` · ${indicator.issues}`;
                } else if (typeof indicator?.score === "number") {
                  suffix = ` · ${indicator.score}`;
                }

                return (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    id={`editor-tab-${id}`}
                    aria-selected={selected}
                    aria-controls={`editor-panel-${id}`}
                    tabIndex={selected ? 0 : -1}
                    onClick={() => onChange(id)}
                    className={cn(
                      "relative min-h-11 shrink-0 scroll-mx-10 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      selected
                        ? "bg-accent/70 text-foreground after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full after:bg-primary"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                    data-testid={`editor-tab-${id}`}
                  >
                    {EDITOR_TAB_LABEL[id]}
                    {suffix}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {/* Edge fades: purely visual, so hidden from assistive technology. */}
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-y-0 left-0 w-10 bg-gradient-to-r from-background to-transparent transition-opacity",
          edges.left ? "opacity-100" : "opacity-0",
        )}
      />
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-background to-transparent transition-opacity",
          edges.right ? "opacity-100" : "opacity-0",
        )}
      />

      {/* Pointer affordance from `md` up. Keyboard users have Arrow/Home/End. */}
      <button
        type="button"
        tabIndex={-1}
        aria-hidden="true"
        onClick={() => scrollBy(-1)}
        className={cn(
          "absolute left-0 top-1/2 hidden h-11 w-8 -translate-y-1/2 items-center justify-center rounded-md border bg-background/95 text-muted-foreground shadow-sm hover:text-foreground md:flex",
          edges.left ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        data-testid="editor-tabs-scroll-left"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <button
        type="button"
        tabIndex={-1}
        aria-hidden="true"
        onClick={() => scrollBy(1)}
        className={cn(
          "absolute right-0 top-1/2 hidden h-11 w-8 -translate-y-1/2 items-center justify-center rounded-md border bg-background/95 text-muted-foreground shadow-sm hover:text-foreground md:flex",
          edges.right ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        data-testid="editor-tabs-scroll-right"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}
