"use client";

import { Plus, Tags } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ConflictBanner } from "@/components/global-rules/conflict-banner";
import {
  PricingRuleForm,
  emptyPricingRule,
  pricingFormToPayload,
  pricingRuleToForm,
  type PricingRuleFormValues,
} from "@/components/global-rules/pricing-rule-form";
import { RuleHistoryPanel } from "@/components/global-rules/rule-history-panel";
import { RuleList, type RuleListRow } from "@/components/global-rules/rule-list";
import { trimDecimal } from "@/components/global-rules/rule-primitives";
import { useRuleForm } from "@/components/global-rules/rule-form-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  useCreatePricingRule,
  usePricingRuleList,
  useSetPricingRuleActive,
  useUpdatePricingRule,
  type PricingRule,
} from "@/services/global-rules";
import type { Store } from "@/services/stores";

/**
 * Pricing rules: list, create, edit, activate, and read history.
 *
 * Editing happens in a dialog rather than inline so that only one rule is ever
 * dirty at a time — which is what makes "you have unsaved changes" a
 * answerable question rather than a per-row guess.
 */

function summarise(rule: PricingRule): string {
  const percent = (value: string | null, fallback: string) =>
    value === null ? fallback : trimDecimal(value);
  const money = (value: string | null, fallback: string) =>
    value === null ? fallback : trimDecimal(value, 2);

  switch (rule.strategy) {
    case "percentage_markup":
      return `${percent(rule.markupPercent, "?")}% markup on landed cost`;
    case "fixed_markup":
      return `${money(rule.markupFixed, "?")} fixed profit`;
    case "target_margin":
      return `${percent(rule.marginPercent, "?")}% target gross margin`;
    case "hybrid":
      return `${percent(rule.markupPercent, "0")}% markup plus ${money(rule.markupFixed, "0")}`;
    case "tiered":
      return `Tiered — ${rule.tiers.length} tier${rule.tiers.length === 1 ? "" : "s"}`;
    default:
      return "Custom strategy";
  }
}

function toRow(rule: PricingRule): RuleListRow {
  return {
    id: rule.id,
    name: rule.name,
    scope: rule.scope,
    storeId: rule.storeId,
    categoryId: rule.categoryId,
    productId: rule.productId,
    variantId: rule.variantId,
    priority: rule.priority,
    version: rule.version,
    isActive: rule.isActive,
    updatedAt: rule.updatedAt,
    summary: summarise(rule),
  };
}

type EditorState =
  | { mode: "closed" }
  | { mode: "create" }
  | { mode: "edit"; rule: PricingRule };

export function PricingRulesPanel({
  canManage,
  stores,
}: {
  canManage: boolean;
  stores: Store[];
}) {
  const list = usePricingRuleList({ size: 50, sortBy: "priority", sortDir: "desc" });
  const [editor, setEditor] = useState<EditorState>({ mode: "closed" });
  const [historyFor, setHistoryFor] = useState<PricingRule | null>(null);
  const [activationError, setActivationError] = useState<string | null>(null);
  const [busyRuleId, setBusyRuleId] = useState<string | null>(null);

  const rules = useMemo(() => list.data?.items ?? [], [list.data]);
  const storeNames = useMemo(
    () => Object.fromEntries(stores.map((store) => [store.id, store.name])),
    [stores],
  );
  const storeOptions = useMemo(
    () => stores.map((store) => ({ id: store.id, name: store.name })),
    [stores],
  );

  // A stable initial value per editor session. Recomputing it on each render
  // would make the form re-seed itself and discard the merchant's typing.
  const initialValues = useMemo<PricingRuleFormValues>(
    () =>
      editor.mode === "edit" ? pricingRuleToForm(editor.rule) : emptyPricingRule(),
    [editor],
  );
  const form = useRuleForm(initialValues);

  const create = useCreatePricingRule();
  const update = useUpdatePricingRule(
    editor.mode === "edit" ? editor.rule.id : "",
  );
  const activation = useSetPricingRuleActive(busyRuleId ?? "");

  const close = useCallback(() => setEditor({ mode: "closed" }), []);

  async function submit() {
    const saved = await form.submit(async (values) =>
      editor.mode === "edit"
        ? update.mutateAsync({
            ...pricingFormToPayload(values),
            expectedUpdatedAt: editor.rule.updatedAt,
          })
        : create.mutateAsync(pricingFormToPayload(values)),
    );
    if (saved) close();
  }

  async function toggleActive(row: RuleListRow) {
    setActivationError(null);
    setBusyRuleId(row.id);
    try {
      await activation.mutateAsync({
        isActive: !row.isActive,
        expectedUpdatedAt: row.updatedAt,
        note: row.isActive ? "Deactivated from settings" : "Activated from settings",
      });
    } catch (err) {
      setActivationError(
        err instanceof ApiError && err.status === 409
          ? "This rule changed while the page was open. Reload the list and try again."
          : err instanceof ApiError
            ? err.message
            : "The rule could not be updated.",
      );
    } finally {
      setBusyRuleId(null);
    }
  }

  if (list.isLoading) {
    return (
      <div className="space-y-3" role="status" aria-live="polite">
        <span className="sr-only">Loading pricing rules</span>
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }

  if (list.isError) {
    return (
      <ErrorState
        title="Could not load pricing rules"
        description="The rules could not be read. Nothing has been changed."
        requestId={list.error instanceof ApiError ? list.error.requestId : null}
        onRetry={() => void list.refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {rules.length} rule{rules.length === 1 ? "" : "s"}. The narrowest
          matching scope wins.
        </p>
        {canManage && (
          <Button
            type="button"
            className="min-h-10"
            onClick={() => setEditor({ mode: "create" })}
            data-testid="new-pricing-rule"
          >
            <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
            New pricing rule
          </Button>
        )}
      </div>

      {activationError && (
        <p role="alert" className="text-sm text-destructive">
          {activationError}
        </p>
      )}

      {rules.length === 0 ? (
        <EmptyState
          icon={Tags}
          title="No pricing rules yet"
          description="Imported products keep their supplier price until a rule exists."
          action={
            canManage ? (
              <Button
                type="button"
                className="min-h-10"
                onClick={() => setEditor({ mode: "create" })}
              >
                <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
                Create the first rule
              </Button>
            ) : undefined
          }
        />
      ) : (
        <RuleList
          rows={rules.map(toRow)}
          storeNames={storeNames}
          canManage={canManage}
          busyRuleId={busyRuleId}
          onEdit={(id) => {
            const rule = rules.find((item) => item.id === id);
            if (rule) setEditor({ mode: "edit", rule });
          }}
          onToggleActive={(row) => void toggleActive(row)}
          onHistory={(id) => {
            const rule = rules.find((item) => item.id === id);
            if (rule) setHistoryFor(rule);
          }}
        />
      )}

      <Dialog
        open={editor.mode !== "closed"}
        onOpenChange={(open) => {
          if (open) return;
          // Closing with unsaved edits must be a decision, not an accident.
          if (form.isDirty && !window.confirm("Discard your unsaved changes?")) {
            return;
          }
          close();
        }}
      >
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {editor.mode === "edit" ? "Edit pricing rule" : "New pricing rule"}
            </DialogTitle>
            <DialogDescription>
              Saving changes no existing product. Rules apply to imports from
              now on.
            </DialogDescription>
          </DialogHeader>

          {form.conflict && editor.mode === "edit" && (
            <ConflictBanner
              message={form.conflict.message}
              reloading={list.isFetching}
              onReload={async () => {
                const refreshed = await list.refetch();
                const latest = refreshed.data?.items.find(
                  (item) => item.id === editor.rule.id,
                );
                if (latest) {
                  setEditor({ mode: "edit", rule: latest });
                  form.reset(pricingRuleToForm(latest));
                }
              }}
              onKeepEditing={form.dismissConflict}
            />
          )}

          {form.error && (
            <p role="alert" className="text-sm text-destructive">
              {form.error.message}
            </p>
          )}

          <PricingRuleForm
            form={form}
            stores={storeOptions}
            onSubmit={() => void submit()}
            onCancel={close}
            submitLabel={editor.mode === "edit" ? "Save changes" : "Create rule"}
            busy={form.saveState === "saving"}
            showNote={editor.mode === "edit"}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={historyFor !== null} onOpenChange={() => setHistoryFor(null)}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Rule history</DialogTitle>
            <DialogDescription>
              An append-only record of every change. It cannot be edited.
            </DialogDescription>
          </DialogHeader>
          {historyFor && (
            <RuleHistoryPanel
              kind="pricing"
              ruleId={historyFor.id}
              ruleName={historyFor.name}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
