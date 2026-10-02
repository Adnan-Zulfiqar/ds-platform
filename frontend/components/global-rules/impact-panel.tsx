"use client";

import { PackageSearch } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import {
  ApplyConfirmDialog,
  type ApplySummary,
} from "@/components/global-rules/apply-confirm-dialog";
import { ApplicationProgress } from "@/components/global-rules/application-progress";
import { ImpactRow } from "@/components/global-rules/impact-row";
import { Callout, Select, Toggle } from "@/components/global-rules/rule-primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  useApplyToDrafts,
  useDraftImpact,
  type ImpactQuery,
  type PricingRule,
} from "@/services/global-rules";

/**
 * Preview and Impact: what the active rules would do to existing drafts, and
 * the confirmed application that makes it real.
 *
 * **Selection never materialises the catalogue.** A page holds at most 25
 * rows. "Select all matching" does not enumerate anything — it records the
 * *filter*, and the server expands it once, at confirmation, into a durable
 * snapshot. Fabricating 5,000 identifiers in the browser to post them back
 * would be slow, fragile, and wrong the moment the catalogue moved.
 *
 * The application id lives in the URL. That is what makes a browser refresh
 * resume the run being watched instead of losing it, and it costs nothing —
 * no storage, no duplicate application, and the link can be shared.
 */

const PAGE_SIZE = 25;

type SelectionMode = { kind: "ids"; ids: Set<string> } | { kind: "filter" };

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function ImpactPanel({
  canManage,
  rules,
}: {
  canManage: boolean;
  rules: PricingRule[];
}) {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [needsReviewOnly, setNeedsReviewOnly] = useState(false);
  const [safeOnly, setSafeOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [selection, setSelection] = useState<SelectionMode>({
    kind: "ids",
    ids: new Set(),
  });
  const [confirming, setConfirming] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  // Resume whatever run the URL names, so a refresh does not lose it.
  const searchParams = useSearchParams();
  const [applicationId, setApplicationId] = useState<string | null>(() =>
    searchParams.get("application"),
  );

  // One key per confirmation, generated when the dialog opens. A double-click,
  // a retry after a timeout, or a refresh mid-flight all carry the same key
  // and return the same run rather than starting a second one.
  const idempotencyKey = useRef<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);


  const setApplicationInUrl = useCallback((id: string | null) => {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("application", id);
    else url.searchParams.delete("application");
    window.history.replaceState({}, "", url);
    setApplicationId(id);
  }, []);

  const query: ImpactQuery = useMemo(
    () => ({
      page,
      size: PAGE_SIZE,
      search: debouncedSearch || undefined,
      needsReviewOnly,
      safeOnly,
    }),
    [page, debouncedSearch, needsReviewOnly, safeOnly],
  );

  const impact = useDraftImpact(query);
  const apply = useApplyToDrafts();

  const items = useMemo(() => impact.data?.items ?? [], [impact.data]);
  const totalPages = Math.max(1, Math.ceil((impact.data?.total ?? 0) / PAGE_SIZE));

  const selectedIds = selection.kind === "ids" ? selection.ids : null;
  const selectedCount =
    selection.kind === "filter"
      ? (impact.data?.selectableTotal ?? 0)
      : (selectedIds?.size ?? 0);

  const pageSelectable = items.filter((item) => item.canApply);
  const allPageSelected =
    pageSelectable.length > 0 &&
    selection.kind === "ids" &&
    pageSelectable.every((item) => selectedIds?.has(item.productId));

  function toggleOne(productId: string, next: boolean) {
    setSelection((current) => {
      const ids = new Set(current.kind === "ids" ? current.ids : []);
      if (next) ids.add(productId);
      else ids.delete(productId);
      return { kind: "ids", ids };
    });
  }

  function toggleCurrentPage(next: boolean) {
    setSelection((current) => {
      const ids = new Set(current.kind === "ids" ? current.ids : []);
      for (const item of pageSelectable) {
        if (next) ids.add(item.productId);
        else ids.delete(item.productId);
      }
      return { kind: "ids", ids };
    });
  }

  function clearSelection() {
    setSelection({ kind: "ids", ids: new Set() });
  }

  const summary: ApplySummary = useMemo(() => {
    if (selection.kind === "filter") {
      const total = impact.data?.selectableTotal ?? 0;
      return {
        selected: total,
        safe: total,
        // A filter selection excludes held and published drafts server-side,
        // so there are none to report — stating 0 is accurate, not a guess.
        needsReview: 0,
        published: 0,
        estimatedChanges: total,
        ruleName: null,
        ruleVersion: null,
        shippingRuleVersion: null,
        fromFilter: true,
      };
    }
    const chosen = items.filter((item) => selectedIds?.has(item.productId));
    const safe = chosen.filter((item) => item.canApply);
    const changing = safe.filter((item) => item.proposedPrice !== item.currentPrice);
    const rule = safe[0];
    return {
      // Selected ids may span pages; only the visible ones can be classified,
      // which is why the "safe" figure is drawn from what is on screen.
      selected: selectedIds?.size ?? 0,
      safe: safe.length,
      needsReview: chosen.filter((item) => item.needsReview && !item.published).length,
      published: chosen.filter((item) => item.published).length,
      estimatedChanges: changing.length,
      ruleName:
        rule?.pricingRuleId
          ? (rules.find((r) => r.id === rule.pricingRuleId)?.name ?? null)
          : null,
      ruleVersion: rule?.pricingRuleVersion ?? null,
      shippingRuleVersion: rule?.shippingRuleVersion ?? null,
      fromFilter: false,
    };
  }, [selection, selectedIds, items, impact.data, rules]);

  function openConfirm() {
    idempotencyKey.current = newIdempotencyKey();
    setApplyError(null);
    setConfirming(true);
  }

  async function confirmApply() {
    if (!idempotencyKey.current) return;
    setApplyError(null);
    try {
      const created = await apply.mutateAsync(
        selection.kind === "filter"
          ? {
              idempotencyKey: idempotencyKey.current,
              selectionFilter: {
                search: debouncedSearch || null,
                needsReviewOnly,
                safeOnly: true,
              },
            }
          : {
              idempotencyKey: idempotencyKey.current,
              productIds: [...(selectedIds ?? [])],
            },
      );
      setConfirming(false);
      clearSelection();
      setApplicationInUrl(created.id);
    } catch (err) {
      setApplyError(
        err instanceof ApiError && err.status === 409
          ? "That confirmation was already used for a different selection. Close this and start again."
          : err instanceof ApiError && err.status === 429
            ? "Too many applications started just now. Wait a moment and try again."
            : err instanceof ApiError
              ? err.message
              : "The application could not be started.",
      );
    }
  }

  if (applicationId) {
    return (
      <ApplicationProgress
        applicationId={applicationId}
        canManage={canManage}
        onDismiss={() => setApplicationInUrl(null)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <Callout>
        <p>
          What the active rules would do to your existing drafts. Nothing here
          changes a price until you confirm — and published products are never
          repriced by this workflow.
        </p>
      </Callout>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="space-y-1.5">
          <span className="text-sm font-medium">Search</span>
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Product title…"
            data-testid="impact-search"
          />
        </label>
        <div className="flex items-end">
          <Toggle
            checked={needsReviewOnly}
            onChange={(next) => {
              setNeedsReviewOnly(next);
              setPage(1);
            }}
            label="Needs review only"
          />
        </div>
        <div className="flex items-end">
          <Toggle
            checked={safeOnly}
            onChange={(next) => {
              setSafeOnly(next);
              setPage(1);
            }}
            label="Ready to price only"
            description="Hides published and held drafts."
          />
        </div>
        <div className="flex items-end">
          <label className="w-full space-y-1.5">
            <span className="text-sm font-medium">Page</span>
            <Select
              value={String(page)}
              onChange={(value) => setPage(Number(value))}
              options={Array.from({ length: totalPages }, (_, index) => ({
                value: String(index + 1),
                label: `${index + 1} of ${totalPages}`,
              }))}
            />
          </label>
        </div>
      </div>

      {impact.isLoading ? (
        <div className="space-y-3" role="status" aria-live="polite">
          <span className="sr-only">Loading draft impact</span>
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
        </div>
      ) : impact.isError ? (
        <ErrorState
          title="Could not load the impact preview"
          description="Nothing has been changed."
          requestId={impact.error instanceof ApiError ? impact.error.requestId : null}
          onRetry={() => void impact.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={PackageSearch}
          title="No drafts match"
          description="Import some products, or widen the filters above."
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
            <span className="text-sm" data-testid="selection-summary" aria-live="polite">
              <strong>{selectedCount}</strong> selected
              {selection.kind === "filter" && " (everything matching)"}
            </span>
            <Badge variant="outline">{impact.data?.applicableCount ?? 0} ready here</Badge>
            <Badge variant="outline">{impact.data?.publishedCount ?? 0} published</Badge>

            {canManage && (
              <div className="ml-auto flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="min-h-10"
                  onClick={() => toggleCurrentPage(!allPageSelected)}
                  data-testid="select-page"
                >
                  {allPageSelected ? "Deselect page" : "Select page"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="min-h-10"
                  onClick={() => setSelection({ kind: "filter" })}
                  data-testid="select-all-matching"
                >
                  Select all {impact.data?.selectableTotal ?? 0} matching
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="min-h-10"
                  onClick={clearSelection}
                  data-testid="clear-selection"
                >
                  Clear
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="min-h-10"
                  disabled={selectedCount === 0}
                  onClick={openConfirm}
                  data-testid="open-confirm"
                >
                  Apply to selected
                </Button>
              </div>
            )}
          </div>

          {selection.kind === "filter" &&
            (impact.data?.matchingTotal ?? 0) >
              (impact.data?.maxApplicationProducts ?? 0) && (
              <Callout tone="warning">
                <p>
                  {impact.data?.matchingTotal} drafts match, and one application
                  covers at most {impact.data?.maxApplicationProducts}. The
                  first {impact.data?.selectableTotal} will be applied; run it
                  again afterwards for the rest.
                </p>
              </Callout>
            )}

          <ul className="space-y-3" data-testid="impact-list">
            {items.map((item) => (
              <ImpactRow
                key={item.productId}
                item={item}
                selectable={canManage && item.canApply}
                selected={
                  selection.kind === "filter"
                    ? item.canApply
                    : Boolean(selectedIds?.has(item.productId))
                }
                onToggle={toggleOne}
              />
            ))}
          </ul>

          <nav
            className="flex flex-wrap items-center justify-between gap-2"
            aria-label="Impact pagination"
          >
            <p className="text-xs text-muted-foreground" aria-live="polite">
              Page {page} of {totalPages} · {impact.data?.total ?? 0} drafts
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-10"
                disabled={page <= 1}
                onClick={() => setPage((current) => current - 1)}
              >
                Previous
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-10"
                disabled={page >= totalPages}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </Button>
            </div>
          </nav>
        </>
      )}

      <ApplyConfirmDialog
        open={confirming}
        summary={summary}
        busy={apply.isPending}
        error={applyError}
        onConfirm={() => void confirmApply()}
        onCancel={() => {
          // Closing writes nothing: there is no request behind this path.
          setConfirming(false);
          setApplyError(null);
        }}
      />
    </div>
  );
}
