"use client";

import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Callout,
  DecimalInput,
  Field,
  SCOPE_LABEL,
  Select,
  trimDecimal,
} from "@/components/global-rules/rule-primitives";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import {
  usePreviewRule,
  type PreviewRequest,
  type PreviewResult,
  type PricingRule,
} from "@/services/global-rules";

/**
 * A read-only calculator over the backend preview endpoint.
 *
 * **Every number here comes from the server.** Not one figure is computed in
 * TypeScript — not the landed cost, not the margin, not the rounding. That is
 * the entire point: the preview must be the same arithmetic that will price
 * the product, and a second implementation would eventually disagree with the
 * first, which is worse than having no preview at all.
 *
 * The endpoint writes nothing. It prices a transient product that is never
 * added to a database session.
 */

const REVIEW_REASON_COPY: Record<string, string> = {
  supplier_cost_unknown:
    "The supplier did not report an item cost, so there is nothing to price from.",
  shipping_cost_unknown:
    "The supplier did not report a freight cost. No price is produced rather than assuming zero.",
  supplier_currency_unknown:
    "The product has no currency, so the amounts cannot be interpreted.",
  fx_rate_unavailable:
    "This rule is denominated in a different currency and no exchange rate is available here.",
  no_shipping_quotes_available:
    "A shipping rule applies but the supplier offered no priced option.",
  no_shipping_method_matches_rule:
    "Quotes were available but none satisfied the shipping rule.",
  no_shipping_method_matches:
    "Quotes were available but none satisfied the shipping rule.",
  shipping_rule_not_satisfied_fallback_used:
    "No option met the shipping rule, so the cheapest was used instead.",
  shipping_destination_unknown:
    "Neither the rule nor the product names a destination country.",
  pricing_calculation_failed:
    "This rule could not be evaluated — check that its strategy has the field it needs.",
};

const DEBOUNCE_MS = 400;

interface PreviewInputsState {
  itemCost: string;
  shippingCost: string;
  currency: string;
  ruleId: string;
  storeId: string;
  categoryId: string;
  productId: string;
  variantId: string;
}

function toRequest(inputs: PreviewInputsState): PreviewRequest {
  const trim = (value: string) => {
    const t = value.trim();
    return t === "" ? null : t;
  };
  return {
    itemCost: trim(inputs.itemCost),
    shippingCost: trim(inputs.shippingCost),
    currency: trim(inputs.currency)?.toUpperCase() ?? null,
    ruleId: trim(inputs.ruleId),
    storeId: trim(inputs.storeId),
    categoryId: trim(inputs.categoryId),
    productId: trim(inputs.productId),
    variantId: trim(inputs.variantId),
  };
}

function Row({
  label,
  value,
  emphasis,
  testId,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
  testId?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd
        data-testid={testId}
        className={
          emphasis
            ? "font-mono text-base font-semibold tabular-nums"
            : "font-mono text-sm tabular-nums"
        }
      >
        {value}
      </dd>
    </div>
  );
}

export function LivePreviewPanel({
  rules,
  stores,
}: {
  rules: PricingRule[];
  stores: Array<{ id: string; name: string }>;
}) {
  const [inputs, setInputs] = useState<PreviewInputsState>({
    itemCost: "10.00",
    shippingCost: "4.00",
    currency: "GBP",
    ruleId: "",
    storeId: "",
    categoryId: "",
    productId: "",
    variantId: "",
  });
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const preview = usePreviewRule();
  const { mutateAsync } = preview;

  /**
   * Monotonic request counter.
   *
   * Typing fires overlapping requests, and they do not necessarily come back
   * in order — a slow early request can land after a fast later one and
   * repaint the panel with figures for input the merchant has already changed.
   * Only a response whose sequence is still the newest is allowed to write.
   */
  const sequence = useRef(0);

  const run = useCallback(
    async (next: PreviewInputsState) => {
      const ticket = ++sequence.current;
      try {
        const data = await mutateAsync(toRequest(next));
        if (ticket !== sequence.current) return;
        setResult(data);
        setError(null);
      } catch (err) {
        if (ticket !== sequence.current) return;
        setResult(null);
        setError(
          err instanceof ApiError
            ? err.message
            : "The preview could not be calculated.",
        );
      }
    },
    [mutateAsync],
  );

  useEffect(() => {
    const timer = setTimeout(() => void run(inputs), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [inputs, run]);

  const set = <K extends keyof PreviewInputsState>(key: K, value: string) =>
    setInputs((current) => ({ ...current, [key]: value }));

  const currency = inputs.currency.trim().toUpperCase() || "";
  const money = useCallback(
    (value: string | null) =>
      value === null ? "—" : `${currency} ${trimDecimal(value, 2)}`.trim(),
    [currency],
  );
  const percent = (value: string | null) =>
    value === null ? "—" : `${trimDecimal(value)}%`;

  const activeRules = useMemo(() => rules.filter((rule) => rule.isActive), [rules]);
  const busy = preview.isPending;

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="space-y-4">
        <Callout>
          <p>
            Try a rule against sample figures. Nothing is saved and no product
            is changed — every number below is calculated by the same engine
            that prices your products.
          </p>
        </Callout>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Item cost">
            {(props) => (
              <DecimalInput
                {...props}
                value={inputs.itemCost}
                onChange={(value) => set("itemCost", value)}
              />
            )}
          </Field>
          <Field
            label="Supplier shipping"
            description="Leave blank to see what happens when the supplier reports none."
          >
            {(props) => (
              <DecimalInput
                {...props}
                value={inputs.shippingCost}
                onChange={(value) => set("shippingCost", value)}
              />
            )}
          </Field>
          <Field label="Currency">
            {(props) => (
              <Input
                {...props}
                value={inputs.currency}
                maxLength={3}
                onChange={(event) => set("currency", event.target.value.toUpperCase())}
              />
            )}
          </Field>
          <Field
            label="Rule"
            description="Or leave on “Resolve automatically” to see which rule would win."
          >
            {(props) => (
              <Select
                {...props}
                value={inputs.ruleId}
                onChange={(value) => set("ruleId", value)}
                options={[
                  { value: "", label: "Resolve automatically" },
                  ...activeRules.map((rule) => ({ value: rule.id, label: rule.name })),
                ]}
              />
            )}
          </Field>
        </div>

        <details className="rounded-lg border p-4">
          <summary className="min-h-10 cursor-pointer text-sm font-medium">
            Scope context
          </summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Store">
              {(props) => (
                <Select
                  {...props}
                  value={inputs.storeId}
                  onChange={(value) => set("storeId", value)}
                  options={[
                    { value: "", label: "No store" },
                    ...stores.map((store) => ({ value: store.id, label: store.name })),
                  ]}
                />
              )}
            </Field>
            <Field label="Category ID">
              {(props) => (
                <Input
                  {...props}
                  value={inputs.categoryId}
                  onChange={(event) => set("categoryId", event.target.value)}
                />
              )}
            </Field>
            <Field label="Product ID">
              {(props) => (
                <Input
                  {...props}
                  value={inputs.productId}
                  onChange={(event) => set("productId", event.target.value)}
                />
              )}
            </Field>
            <Field label="Variant ID">
              {(props) => (
                <Input
                  {...props}
                  value={inputs.variantId}
                  onChange={(event) => set("variantId", event.target.value)}
                />
              )}
            </Field>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Duty and fees come from the rule itself rather than being entered
            here, so the preview reflects the rule exactly as it will run.
          </p>
        </details>
      </div>

      <div
        className="space-y-4 rounded-lg border p-4"
        data-testid="preview-results"
        aria-busy={busy}
      >
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium">Result</h3>
          {/* Announced politely so a screen reader learns the panel is
              recalculating without the message interrupting typing. */}
          <span
            role="status"
            aria-live="polite"
            className="flex items-center gap-2 text-xs text-muted-foreground"
          >
            {busy && (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                Calculating…
              </>
            )}
          </span>
        </div>

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        {result && (
          <>
            <div className="space-y-1 rounded-md bg-muted/40 p-3">
              <p className="text-xs text-muted-foreground">Governing rule</p>
              <p className="font-medium" data-testid="preview-rule-name">
                {result.resolution.ruleName ?? "No rule matches"}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                {result.resolution.scope && (
                  <Badge variant="outline">{SCOPE_LABEL[result.resolution.scope]}</Badge>
                )}
                {result.resolution.version !== null && (
                  <Badge variant="outline">Version {result.resolution.version}</Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground">{result.resolution.reason}</p>
              {result.resolution.overriddenRuleIds.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  Overrides {result.resolution.overriddenRuleIds.length} broader{" "}
                  {result.resolution.overriddenRuleIds.length === 1 ? "rule" : "rules"}.
                </p>
              )}
            </div>

            <dl className="divide-y">
              <Row label="Item cost" value={money(result.itemCost)} />
              <Row label="Supplier shipping" value={money(result.shippingCost)} />
              <Row label="Duty and fees" value={money(result.fees)} />
              <Row
                label="Landed cost"
                value={money(result.landedCost)}
                testId="preview-landed-cost"
              />
              <Row
                label="Price before rounding"
                value={money(result.priceBeforeRounding)}
                testId="preview-price-before-rounding"
              />
              <Row
                label="Selling price"
                value={money(result.proposedPrice)}
                emphasis
                testId="preview-price"
              />
              <Row
                label="Customer shipping charge"
                value={money(result.separateShippingCharge)}
              />
              <Row label="Compare-at price" value={money(result.compareAtPrice)} />
              <Row label="Profit" value={money(result.profit)} />
              <Row
                label="Markup"
                value={percent(result.markupPercent)}
                testId="preview-markup"
              />
              <Row
                label="Gross margin"
                value={percent(result.marginPercent)}
                testId="preview-margin"
              />
            </dl>

            {result.needsReview && (
              <Callout tone="warning" title="No price can be produced">
                <ul className="list-disc space-y-1 pl-4" data-testid="preview-review-reasons">
                  {result.reviewReasons.map((reason) => (
                    <li key={reason}>{REVIEW_REASON_COPY[reason] ?? reason}</li>
                  ))}
                </ul>
              </Callout>
            )}
          </>
        )}

        {!result && !error && !busy && (
          <p className="text-sm text-muted-foreground">
            Enter a cost to see what your rules would do.
          </p>
        )}
      </div>
    </div>
  );
}
