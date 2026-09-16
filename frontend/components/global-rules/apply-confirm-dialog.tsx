"use client";

import { Loader2 } from "lucide-react";

import { Callout } from "@/components/global-rules/rule-primitives";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * The last screen before prices actually move.
 *
 * Every number here is stated rather than implied, because "apply to 412
 * drafts" is not a decision anyone can make without knowing how many of those
 * are safe, how many are held, and how many will be skipped for being live.
 *
 * Cancelling writes nothing at all — there is no request behind the close
 * button. Confirming sends one request carrying an idempotency key generated
 * once, when this dialog opened: a double-click, a retry after a timeout, or a
 * refresh mid-flight all reach the same key and return the same run.
 */
export interface ApplySummary {
  selected: number;
  safe: number;
  needsReview: number;
  published: number;
  estimatedChanges: number;
  ruleName: string | null;
  ruleVersion: number | null;
  shippingRuleVersion: number | null;
  /** True when the selection came from "everything matching". */
  fromFilter: boolean;
}

function Row({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd data-testid={testId} className="font-mono text-sm tabular-nums">
        {value}
      </dd>
    </div>
  );
}

export function ApplyConfirmDialog({
  open,
  summary,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  summary: ApplySummary;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onCancel())}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Apply rules to these drafts?</DialogTitle>
          <DialogDescription>
            This writes new prices to your drafts. Nothing is sent to any sales
            channel.
          </DialogDescription>
        </DialogHeader>

        <dl className="divide-y" data-testid="confirm-summary">
          <Row
            label="Drafts selected"
            value={
              summary.fromFilter
                ? `${summary.selected} (everything matching)`
                : String(summary.selected)
            }
            testId="confirm-selected"
          />
          <Row label="Ready to price" value={String(summary.safe)} testId="confirm-safe" />
          <Row
            label="Held for review"
            value={String(summary.needsReview)}
            testId="confirm-review"
          />
          <Row
            label="Published — will be skipped"
            value={String(summary.published)}
            testId="confirm-published"
          />
          <Row
            label="Expected price changes"
            value={String(summary.estimatedChanges)}
            testId="confirm-changes"
          />
          <Row
            label="Pricing rule"
            value={
              summary.ruleName === null
                ? "resolved per product"
                : `${summary.ruleName} · v${summary.ruleVersion ?? "?"}`
            }
          />
          <Row
            label="Shipping rule"
            value={
              summary.shippingRuleVersion === null
                ? "none"
                : `version ${summary.shippingRuleVersion}`
            }
          />
        </dl>

        <Callout tone="warning" title="These are real writes">
          <p>
            Draft prices that change are changed for good — there is no undo.
            Every product is recorded with what happened to it, including the
            ones nothing happened to.
          </p>
          <p className="mt-2">
            <strong>Published products and Shopify are untouched.</strong> A
            product that is already on Shopify is recorded as skipped; no request
            is made to any sales channel.
          </p>
        </Callout>

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            onClick={onConfirm}
            disabled={busy || summary.safe === 0}
            className="min-h-10"
            data-testid="confirm-apply"
          >
            {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />}
            Apply to {summary.safe} draft{summary.safe === 1 ? "" : "s"}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={busy}
            className="min-h-10"
          >
            Cancel
          </Button>
        </div>

        {summary.safe === 0 && (
          <p className="text-xs text-muted-foreground">
            Nothing in this selection can be priced yet. Resolve the reasons
            above, or choose different drafts.
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
