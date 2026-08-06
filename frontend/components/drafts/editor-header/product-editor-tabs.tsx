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

export const EDITOR_TAB_LABEL: Record<EditorTab, string> = {
  overview: "Overview",
  description: "Description",
  media: "Media",
  variants: "Variants",
  pricing: "Pricing",
  inventory: "Inventory",
  shipping: "Shipping",
  seo: "SEO",
  "ai-studio": "AI Studio",
  publishing: "Publishing",
  history: "History",
};

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

  const focusTab = useCallback((index: number) => {
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]',
    );
    buttons?.[index]?.focus();
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = EDITOR_TABS.indexOf(activeTab);
    if (current < 0) return;

    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      const next = (current + 1) % EDITOR_TABS.length;
      const nextTab = EDITOR_TABS[next];
      if (!nextTab) return;
      onChange(nextTab);
      focusTab(next);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      const prev = (current - 1 + EDITOR_TABS.length) % EDITOR_TABS.length;
      const prevTab = EDITOR_TABS[prev];
      if (!prevTab) return;
      onChange(prevTab);
      focusTab(prev);
    } else if (event.key === "Home") {
      event.preventDefault();
      const first = EDITOR_TABS[0];
      if (!first) return;
      onChange(first);
      focusTab(0);
    } else if (event.key === "End") {
      event.preventDefault();
      const last = EDITOR_TABS.length - 1;
      const lastTab = EDITOR_TABS[last];
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
        className="flex gap-0.5 overflow-x-auto pb-px [scrollbar-width:thin]"
        data-testid="product-editor-tabs"
      >
        {EDITOR_TABS.map((id) => {
          const selected = activeTab === id;
          const indicator = indicators?.[id];
          let suffix = "";
          if (indicator?.blocked) suffix = " · Blocked";
          else if (typeof indicator?.issues === "number" && indicator.issues > 0) {
            suffix = ` · ${indicator.issues} issue${indicator.issues === 1 ? "" : "s"}`;
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
                "relative shrink-0 rounded-md px-3 py-2 text-sm font-medium transition-colors",
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
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-background to-transparent md:hidden"
      />
    </div>
  );
}
