"use client";

import { Plus, Truck } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ConflictBanner } from "@/components/global-rules/conflict-banner";
import { RuleHistoryPanel } from "@/components/global-rules/rule-history-panel";
import { RuleList, type RuleListRow } from "@/components/global-rules/rule-list";
import { trimDecimal } from "@/components/global-rules/rule-primitives";
import { useRuleForm } from "@/components/global-rules/rule-form-state";
import {
  MissingFreightWarning,
  ShippingRuleForm,
  emptyShippingRule,
  shippingFormToPayload,
  shippingRuleToForm,
  type ShippingRuleFormValues,
} from "@/components/global-rules/shipping-rule-form";
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
  useCreateShippingRule,
  useSetShippingRuleActive,
  useShippingRuleList,
  useUpdateShippingRule,
  type ShippingRule,
} from "@/services/global-rules";
import type { Store } from "@/services/stores";

const STRATEGY_LABEL: Record<ShippingRule["selectionStrategy"], string> = {
  cheapest: "Cheapest",
  cheapest_tracked: "Cheapest tracked",
  fastest: "Fastest",
  fastest_under_cost: "Fastest below maximum cost",
};

function summarise(rule: ShippingRule): string {
  const base = `${STRATEGY_LABEL[rule.selectionStrategy]} to ${
    rule.destinationCountry ?? "any destination"
  }`;
  const extras: string[] = [];
  if (rule.trackingRequired) extras.push("tracking required");
  if (rule.maxDeliveryDays) extras.push(`≤ ${rule.maxDeliveryDays} days`);
  if (rule.maxShippingCost) extras.push(`≤ ${trimDecimal(rule.maxShippingCost, 2)}`);
  return extras.length > 0 ? `${base} · ${extras.join(" · ")}` : base;
}

function toRow(rule: ShippingRule): RuleListRow {
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
  | { mode: "edit"; rule: ShippingRule };

export function ShippingRulesPanel({
  canManage,
  stores,
}: {
  canManage: boolean;
  stores: Store[];
}) {
  const list = useShippingRuleList({ size: 50, sortBy: "priority", sortDir: "desc" });
  const [editor, setEditor] = useState<EditorState>({ mode: "closed" });
  const [historyFor, setHistoryFor] = useState<ShippingRule | null>(null);
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

  const initialValues = useMemo<ShippingRuleFormValues>(
    () =>
      editor.mode === "edit" ? shippingRuleToForm(editor.rule) : emptyShippingRule(),
    [editor],
  );
  const form = useRuleForm(initialValues);

  const create = useCreateShippingRule();
  const update = useUpdateShippingRule(editor.mode === "edit" ? editor.rule.id : "");
  const activation = useSetShippingRuleActive(busyRuleId ?? "");

  const close = useCallback(() => setEditor({ mode: "closed" }), []);

  async function submit() {
    const saved = await form.submit(async (values) =>
      editor.mode === "edit"
        ? update.mutateAsync({
            ...shippingFormToPayload(values),
            expectedUpdatedAt: editor.rule.updatedAt,
          })
        : create.mutateAsync(shippingFormToPayload(values)),
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
        <span className="sr-only">Loading shipping rules</span>
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }

  if (list.isError) {
    return (
      <ErrorState
        title="Could not load shipping rules"
        description="The rules could not be read. Nothing has been changed."
        requestId={list.error instanceof ApiError ? list.error.requestId : null}
        onRetry={() => void list.refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      <MissingFreightWarning />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {rules.length} rule{rules.length === 1 ? "" : "s"}. A shipping rule
          chooses between the quotes a supplier returns.
        </p>
        {canManage && (
          <Button
            type="button"
            className="min-h-10"
            onClick={() => setEditor({ mode: "create" })}
            data-testid="new-shipping-rule"
          >
            <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
            New shipping rule
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
          icon={Truck}
          title="No shipping rules yet"
          description="Without a rule, the cheapest priced supplier option is used and nothing is flagged."
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
          if (form.isDirty && !window.confirm("Discard your unsaved changes?")) {
            return;
          }
          close();
        }}
      >
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {editor.mode === "edit" ? "Edit shipping rule" : "New shipping rule"}
            </DialogTitle>
            <DialogDescription>
              Chooses among supplier shipping options. It never invents a cost.
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
                  form.reset(shippingRuleToForm(latest));
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

          <ShippingRuleForm
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
              kind="shipping"
              ruleId={historyFor.id}
              ruleName={historyFor.name}
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
