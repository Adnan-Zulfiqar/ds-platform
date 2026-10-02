"use client";

import { useCallback, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "next/navigation";

import { ApplicationBehaviourPanel } from "@/components/global-rules/application-behaviour-panel";
import { ImpactPanel } from "@/components/global-rules/impact-panel";
import { LivePreviewPanel } from "@/components/global-rules/live-preview-panel";
import { PricingRulesPanel } from "@/components/global-rules/pricing-rules-panel";
import { RuleHistoryPanel } from "@/components/global-rules/rule-history-panel";
import { ShippingRulesPanel } from "@/components/global-rules/shipping-rules-panel";
import { Callout, Select } from "@/components/global-rules/rule-primitives";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/providers/auth-provider";
import { cn } from "@/lib/utils";
import {
  usePricingRuleList,
  useShippingRuleList,
  type RuleKind,
} from "@/services/global-rules";
import { useStores } from "@/services/stores";

/**
 * Settings → Global Rules.
 *
 * Five areas on one page rather than five routes: they are read together —
 * a merchant edits a rule, previews what it does, and checks the history of
 * what they changed — and separate routes would mean re-fetching the same
 * rule list on each hop.
 *
 * Preview and Impact sits alongside them but behind its own confirmation:
 * bulk repricing is a different kind of action from configuring a rule, and
 * the boundary that keeps a settings save from repricing a catalogue is the
 * explicit confirm step, not the tab it lives under.
 */

const SECTIONS = [
  { id: "pricing", label: "Pricing Rules" },
  { id: "shipping", label: "Shipping Rules" },
  { id: "behaviour", label: "Application Behaviour" },
  { id: "preview", label: "Live Preview" },
  { id: "impact", label: "Preview and Impact" },
  { id: "history", label: "Rule History" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

function SectionTabs({
  active,
  onChange,
}: {
  active: SectionId;
  onChange: (id: SectionId) => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  const focusTab = useCallback((index: number) => {
    const buttons =
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    buttons?.[index]?.focus();
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = SECTIONS.findIndex((section) => section.id === active);
    if (current < 0) return;
    let next: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = (current + 1) % SECTIONS.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = (current - 1 + SECTIONS.length) % SECTIONS.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = SECTIONS.length - 1;
    }
    const target = next === null ? undefined : SECTIONS[next];
    if (!target) return;
    event.preventDefault();
    onChange(target.id);
    focusTab(next as number);
  };

  return (
    // Horizontal scroll is contained here rather than escaping to the page,
    // so five tabs on a 375px screen scroll within their own strip.
    <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <div
        ref={listRef}
        role="tablist"
        aria-label="Global rules sections"
        onKeyDown={onKeyDown}
        className="inline-flex min-w-full gap-1 border-b"
      >
        {SECTIONS.map((section) => {
          const selected = section.id === active;
          return (
            <button
              key={section.id}
              type="button"
              role="tab"
              id={`tab-${section.id}`}
              aria-selected={selected}
              aria-controls={`panel-${section.id}`}
              // Roving tabindex: one stop for the whole tablist, then arrow
              // keys move between tabs. Tabbing through all five would put the
              // panel four presses away from the strip.
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(section.id)}
              className={cn(
                "min-h-10 whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                selected
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {section.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function isSectionId(value: string | null): value is SectionId {
  return SECTIONS.some((section) => section.id === value);
}

export function GlobalRulesWorkspace() {
  const searchParams = useSearchParams();
  const [active, setActiveState] = useState<SectionId>(() => {
    const section = searchParams.get("section");
    if (isSectionId(section)) return section;
    return searchParams.get("application") ? "impact" : "pricing";
  });

  // The open section lives in the URL, so a reload comes back to the same
  // place and a link points at it. It also matters for correctness rather
  // than convenience: a run being watched is tracked by `?application=`, and
  // landing back on the first tab after a refresh would hide it entirely --
  // which is exactly what a merchant would read as "my application vanished".

  const setActive = useCallback((next: SectionId) => {
    setActiveState(next);
    const url = new URL(window.location.href);
    url.searchParams.set("section", next);
    if (next !== "impact") url.searchParams.delete("application");
    window.history.replaceState({}, "", url);
  }, []);
  const [historyKind, setHistoryKind] = useState<RuleKind>("pricing");
  const [historyRuleId, setHistoryRuleId] = useState<string>("");

  const { hasRole } = useAuth();
  // Owners and admins manage; everyone else reads. This is presentation only
  // — the API enforces the same boundary and rejects a viewer's write
  // regardless of what the UI renders.
  const canManage = hasRole("owner") || hasRole("admin");

  const pricing = usePricingRuleList({ size: 50, sortBy: "priority", sortDir: "desc" });
  const shipping = useShippingRuleList({ size: 50, sortBy: "priority", sortDir: "desc" });
  const stores = useStores({ size: 100 });

  const pricingRules = pricing.data?.items ?? [];
  const shippingRules = shipping.data?.items ?? [];
  const storeList = stores.data?.items ?? [];

  const historyOptions =
    historyKind === "pricing"
      ? pricingRules.map((rule) => ({ value: rule.id, label: rule.name }))
      : shippingRules.map((rule) => ({ value: rule.id, label: rule.name }));
  const selectedHistoryRule =
    historyOptions.find((option) => option.value === historyRuleId) ??
    historyOptions[0];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Global Rules"
        description="Pricing and shipping rules applied across your catalogue."
      />

      <Callout>
        <p>
          Rules price products <strong>as they are imported</strong>. Saving a
          rule never changes an existing product, and published products are
          never repriced automatically.
        </p>
      </Callout>

      <SectionTabs active={active} onChange={setActive} />

      <div
        role="tabpanel"
        id={`panel-${active}`}
        aria-labelledby={`tab-${active}`}
        tabIndex={-1}
        className="focus-visible:outline-none"
      >
        {active === "pricing" && (
          <PricingRulesPanel canManage={canManage} stores={storeList} />
        )}

        {active === "shipping" && (
          <ShippingRulesPanel canManage={canManage} stores={storeList} />
        )}

        {active === "behaviour" &&
          (pricing.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <ApplicationBehaviourPanel rules={pricingRules} canManage={canManage} />
          ))}

        {active === "preview" && (
          <LivePreviewPanel
            rules={pricingRules}
            stores={storeList.map((store) => ({ id: store.id, name: store.name }))}
          />
        )}

        {active === "impact" && (
          <ImpactPanel canManage={canManage} rules={pricingRules} />
        )}

        {active === "history" && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="space-y-1.5 text-sm font-medium">
                <span>Rule type</span>
                <Select
                  value={historyKind}
                  onChange={(kind) => {
                    setHistoryKind(kind);
                    setHistoryRuleId("");
                  }}
                  options={[
                    { value: "pricing" as RuleKind, label: "Pricing" },
                    { value: "shipping" as RuleKind, label: "Shipping" },
                  ]}
                />
              </label>
              <label className="space-y-1.5 text-sm font-medium">
                <span>Rule</span>
                <Select
                  value={selectedHistoryRule?.value ?? ""}
                  onChange={setHistoryRuleId}
                  options={
                    historyOptions.length > 0
                      ? historyOptions
                      : [{ value: "", label: "No rules yet" }]
                  }
                />
              </label>
            </div>

            {selectedHistoryRule ? (
              <RuleHistoryPanel
                key={`${historyKind}-${selectedHistoryRule.value}`}
                kind={historyKind}
                ruleId={selectedHistoryRule.value}
                ruleName={selectedHistoryRule.label}
              />
            ) : (
              <EmptyState
                title="No rules to show history for"
                description="Create a rule and every change to it will be recorded here."
              />
            )}
          </div>
        )}
      </div>

      {!canManage && (
        <p className="text-xs text-muted-foreground">
          <Badge variant="outline" className="mr-2">
            Read only
          </Badge>
          Your role can view rules, run previews and read history. Ask an
          administrator to make changes.
        </p>
      )}
    </div>
  );
}
