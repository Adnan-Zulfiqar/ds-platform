"use client";

import {
  useCallback,
  useRef,
  type KeyboardEvent,
} from "react";

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

export function ProductEditorTabs({
  activeTab,
  onChange,
  indicators,
  className,
}: ProductEditorTabsProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const flatTabs = EDITOR_NAV_GROUPS.flatMap((group) => group.tabs);

  const focusTab = useCallback((index: number) => {
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]',
    );
    buttons?.[index]?.focus();
  }, []);

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
    <div className={cn("relative", className)}>
      <div
        ref={listRef}
        role="tablist"
        aria-label="Editor sections"
        onKeyDown={onKeyDown}
        className="flex gap-4 overflow-x-auto pb-px [scrollbar-width:thin] md:gap-6"
        data-testid="product-editor-tabs"
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
                      "relative min-h-11 shrink-0 rounded-md px-3 py-2 text-sm font-medium transition-colors",
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
    </div>
  );
}
