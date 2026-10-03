"use client";

import { Loader2 } from "lucide-react";
import { useMemo, useState } from "react";

import {
  Callout,
  DecimalInput,
  Field,
  MarkupVersusMargin,
  Select,
  Toggle,
} from "@/components/global-rules/rule-primitives";
import {
  RuleScopeFields,
  ScopePrecedenceNote,
  validateScope,
  type ScopeValue,
} from "@/components/global-rules/rule-scope-fields";
import {
  useUnsavedWarning,
  type RuleFormState,
} from "@/components/global-rules/rule-form-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type {
  PriceRounding,
  PricingRule,
  PricingStrategy,
  ShippingCostHandling,
} from "@/services/global-rules";

/**
 * Create/edit form for one pricing rule.
 *
 * **Fields appear only when the selected strategy uses them.** A form showing
 * every field at once invites a merchant to fill in a target margin on a
 * markup rule and wonder why it had no effect — the value would be stored and
 * simply never read.
 *
 * No arithmetic happens here. The worked example in `MarkupVersusMargin` is
 * fixed text; every live number on this screen comes from the preview endpoint.
 */

export const STRATEGY_OPTIONS: ReadonlyArray<{
  value: PricingStrategy;
  label: string;
  hint: string;
}> = [
  {
    value: "fixed_markup",
    label: "Fixed profit",
    hint: "Add a flat amount to landed cost.",
  },
  {
    value: "percentage_markup",
    label: "Markup percentage",
    hint: "Add a percentage of landed cost.",
  },
  {
    value: "target_margin",
    label: "Target gross margin",
    hint: "Price so profit is that share of the selling price.",
  },
  {
    value: "hybrid",
    label: "Hybrid",
    hint: "A percentage of cost plus a flat amount.",
  },
];

const ROUNDING_OPTIONS: ReadonlyArray<{ value: PriceRounding; label: string }> = [
  { value: "none", label: "No rounding" },
  { value: "ninety_nine", label: "Nearest .99" },
  { value: "ninety_five", label: "Nearest .95" },
  { value: "whole", label: "Nearest whole unit" },
];

const SHIPPING_HANDLING_OPTIONS: ReadonlyArray<{
  value: ShippingCostHandling;
  label: string;
  hint: string;
}> = [
  {
    value: "include_in_price",
    label: "Include in the item price",
    hint: "The buyer pays shipping inside the price. This is what a “free shipping” listing means.",
  },
  {
    value: "charge_separately",
    label: "Charge the buyer separately",
    hint: "Landed cost excludes shipping and the figure is surfaced on its own.",
  },
  {
    value: "absorb_from_profit",
    label: "Absorb it from profit",
    hint: "Price as if shipping were free. Profit still counts it, so the margin stays honest.",
  },
];

export interface PricingRuleFormValues extends ScopeValue {
  name: string;
  strategy: PricingStrategy;
  markupPercent: string;
  markupFixed: string;
  marginPercent: string;
  minProfit: string;
  minProfitPerVariant: string;
  minPrice: string;
  maxPrice: string;
  dutyPercent: string;
  feesFixed: string;
  saleFeePercent: string;
  rounding: PriceRounding;
  compareAtPercent: string;
  shippingCostHandling: ShippingCostHandling;
  appliesToNewImports: boolean;
  currency: string;
  isActive: boolean;
  note: string;
}

export function emptyPricingRule(): PricingRuleFormValues {
  return {
    name: "",
    scope: "global",
    priority: 100,
    storeId: null,
    categoryId: null,
    productId: null,
    variantId: null,
    strategy: "percentage_markup",
    markupPercent: "50",
    markupFixed: "",
    marginPercent: "",
    minProfit: "",
    minProfitPerVariant: "",
    minPrice: "",
    maxPrice: "",
    dutyPercent: "",
    feesFixed: "",
    saleFeePercent: "",
    rounding: "none",
    compareAtPercent: "",
    shippingCostHandling: "include_in_price",
    appliesToNewImports: true,
    currency: "",
    isActive: true,
    note: "",
  };
}

export function pricingRuleToForm(rule: PricingRule): PricingRuleFormValues {
  return {
    name: rule.name,
    scope: rule.scope,
    priority: rule.priority,
    storeId: rule.storeId,
    categoryId: rule.categoryId,
    productId: rule.productId,
    variantId: rule.variantId,
    strategy: rule.strategy,
    markupPercent: rule.markupPercent ?? "",
    markupFixed: rule.markupFixed ?? "",
    marginPercent: rule.marginPercent ?? "",
    minProfit: rule.minProfit ?? "",
    minProfitPerVariant: rule.minProfitPerVariant ?? "",
    minPrice: rule.minPrice ?? "",
    maxPrice: rule.maxPrice ?? "",
    dutyPercent: rule.dutyPercent ?? "",
    feesFixed: rule.feesFixed ?? "",
    saleFeePercent: rule.saleFeePercent ?? "",
    rounding: rule.rounding,
    compareAtPercent: rule.compareAtPercent ?? "",
    shippingCostHandling: rule.shippingCostHandling,
    appliesToNewImports: rule.appliesToNewImports,
    currency: rule.currency ?? "",
    isActive: rule.isActive,
    note: "",
  };
}

/** Empty string means "not set", which the API expects as null. */
function optional(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

export function pricingFormToPayload(values: PricingRuleFormValues) {
  return {
    name: values.name.trim(),
    scope: values.scope,
    strategy: values.strategy,
    priority: values.priority,
    storeId: values.storeId,
    categoryId: values.categoryId,
    productId: values.productId,
    variantId: values.variantId,
    markupPercent: optional(values.markupPercent),
    markupFixed: optional(values.markupFixed),
    marginPercent: optional(values.marginPercent),
    minProfit: optional(values.minProfit),
    minProfitPerVariant: optional(values.minProfitPerVariant),
    minPrice: optional(values.minPrice),
    maxPrice: optional(values.maxPrice),
    dutyPercent: optional(values.dutyPercent),
    feesFixed: optional(values.feesFixed),
    saleFeePercent: optional(values.saleFeePercent),
    rounding: values.rounding,
    compareAtPercent: optional(values.compareAtPercent),
    shippingCostHandling: values.shippingCostHandling,
    appliesToNewImports: values.appliesToNewImports,
    tiers: [],
    currency: optional(values.currency)?.toUpperCase() ?? null,
    isActive: values.isActive,
    note: optional(values.note) ?? undefined,
  };
}

export function validatePricingForm(
  values: PricingRuleFormValues,
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!values.name.trim()) errors.name = "Give the rule a name.";

  const scopeError = validateScope(values);
  if (scopeError) errors.scope = scopeError;

  if (values.strategy === "percentage_markup" && !values.markupPercent.trim()) {
    errors.markupPercent = "A markup rule needs a percentage.";
  }
  if (values.strategy === "fixed_markup" && !values.markupFixed.trim()) {
    errors.markupFixed = "A fixed-profit rule needs an amount.";
  }
  if (values.strategy === "target_margin") {
    const margin = Number(values.marginPercent);
    if (!values.marginPercent.trim()) {
      errors.marginPercent = "A target-margin rule needs a percentage.";
    } else if (margin >= 100) {
      errors.marginPercent = "Gross margin must be below 100%.";
    }
  }
  if (
    values.strategy === "hybrid" &&
    !values.markupPercent.trim() &&
    !values.markupFixed.trim()
  ) {
    errors.markupPercent = "A hybrid rule needs a percentage, an amount, or both.";
  }

  if (values.saleFeePercent.trim()) {
    const fee = Number(values.saleFeePercent);
    if (!(fee >= 0 && fee < 100)) {
      errors.saleFeePercent = "The sale fee must be at least 0% and below 100%.";
    } else if (values.strategy === "target_margin" && Number(values.marginPercent) + fee >= 100) {
      errors.saleFeePercent = "Target margin plus the sale fee must be below 100%.";
    }
  }

  const min = Number(values.minPrice);
  const max = Number(values.maxPrice);
  if (values.minPrice.trim() && values.maxPrice.trim() && min > max) {
    errors.maxPrice = "Maximum price cannot be below minimum price.";
  }
  if (values.currency.trim() && !/^[A-Za-z]{3}$/.test(values.currency.trim())) {
    errors.currency = "Use a 3-letter ISO code, for example GBP.";
  }
  return errors;
}

interface PricingRuleFormProps {
  form: RuleFormState<PricingRuleFormValues>;
  stores: Array<{ id: string; name: string }>;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
  busy: boolean;
  /** Only shown when editing — a new rule has no history to annotate. */
  showNote: boolean;
}

export function PricingRuleForm({
  form,
  stores,
  onSubmit,
  onCancel,
  submitLabel,
  busy,
  showNote,
}: PricingRuleFormProps) {
  const { values, setField, setValues, isDirty, fieldErrors } = form;
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});
  useUnsavedWarning(isDirty);

  const errors = useMemo(
    () => ({ ...localErrors, ...fieldErrors }),
    [localErrors, fieldErrors],
  );

  const usesPercent =
    values.strategy === "percentage_markup" || values.strategy === "hybrid";
  const usesFixed =
    values.strategy === "fixed_markup" || values.strategy === "hybrid";
  const usesMargin = values.strategy === "target_margin";

  function submit() {
    const found = validatePricingForm(values);
    setLocalErrors(found);
    if (Object.keys(found).length > 0) return;
    onSubmit();
  }

  return (
    <form
      className="space-y-6"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <Field label="Rule name" required error={errors.name}>
        {(props) => (
          <Input
            {...props}
            value={values.name}
            maxLength={128}
            onChange={(event) => setField("name", event.target.value)}
            placeholder="Standard markup"
          />
        )}
      </Field>

      <RuleScopeFields
        value={values}
        onChange={(next) => setValues((current) => ({ ...current, ...next }))}
        stores={stores}
        error={errors.scope}
      />
      <ScopePrecedenceNote />

      <div className="space-y-4 rounded-lg border p-4">
        <Field
          label="Pricing strategy"
          description={
            STRATEGY_OPTIONS.find((option) => option.value === values.strategy)?.hint
          }
        >
          {(props) => (
            <Select
              {...props}
              value={values.strategy}
              onChange={(strategy) => setField("strategy", strategy)}
              options={STRATEGY_OPTIONS.map(({ value, label }) => ({ value, label }))}
            />
          )}
        </Field>

        <MarkupVersusMargin />

        <div className="grid gap-4 sm:grid-cols-2">
          {usesPercent && (
            <Field
              label="Markup percentage"
              required={values.strategy === "percentage_markup"}
              error={errors.markupPercent}
            >
              {(props) => (
                <DecimalInput
                  {...props}
                  suffix="%"
                  value={values.markupPercent}
                  onChange={(value) => setField("markupPercent", value)}
                />
              )}
            </Field>
          )}
          {usesFixed && (
            <Field
              label="Fixed amount"
              required={values.strategy === "fixed_markup"}
              error={errors.markupFixed}
            >
              {(props) => (
                <DecimalInput
                  {...props}
                  value={values.markupFixed}
                  onChange={(value) => setField("markupFixed", value)}
                />
              )}
            </Field>
          )}
          {usesMargin && (
            <Field
              label="Gross margin percentage"
              required
              error={errors.marginPercent}
              description="Must be below 100%."
            >
              {(props) => (
                <DecimalInput
                  {...props}
                  suffix="%"
                  value={values.marginPercent}
                  onChange={(value) => setField("marginPercent", value)}
                />
              )}
            </Field>
          )}
        </div>
      </div>

      <div className="space-y-4 rounded-lg border p-4">
        <h3 className="text-sm font-medium">Guardrails</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Minimum price" error={errors.minPrice}>
            {(props) => (
              <DecimalInput
                {...props}
                value={values.minPrice}
                onChange={(value) => setField("minPrice", value)}
              />
            )}
          </Field>
          <Field label="Maximum price" error={errors.maxPrice}>
            {(props) => (
              <DecimalInput
                {...props}
                value={values.maxPrice}
                onChange={(value) => setField("maxPrice", value)}
              />
            )}
          </Field>
          <Field
            label="Minimum profit"
            description="Measured against landed cost, so it is real profit."
          >
            {(props) => (
              <DecimalInput
                {...props}
                value={values.minProfit}
                onChange={(value) => setField("minProfit", value)}
              />
            )}
          </Field>
          <Field
            label="Minimum profit per variant"
            description="Applied to each variant's own cost."
          >
            {(props) => (
              <DecimalInput
                {...props}
                value={values.minProfitPerVariant}
                onChange={(value) => setField("minProfitPerVariant", value)}
              />
            )}
          </Field>
        </div>
      </div>

      <div className="space-y-4 rounded-lg border p-4">
        <h3 className="text-sm font-medium">Landed cost and presentation</h3>
        <Callout>
          <p>
            Landed cost is the supplier item price plus supplier shipping plus
            any duty and fees set here. Every guardrail above is measured
            against it, not against the bare item price.
          </p>
        </Callout>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Duty percentage" error={errors.dutyPercent}>
            {(props) => (
              <DecimalInput
                {...props}
                suffix="%"
                value={values.dutyPercent}
                onChange={(value) => setField("dutyPercent", value)}
              />
            )}
          </Field>
          <Field label="Fixed fees">
            {(props) => (
              <DecimalInput
                {...props}
                value={values.feesFixed}
                onChange={(value) => setField("feesFixed", value)}
              />
            )}
          </Field>
          <Field label="Sale fee (% of selling price)" error={errors.saleFeePercent}>
            {(props) => (
              <DecimalInput
                {...props}
                suffix="%"
                value={values.saleFeePercent}
                onChange={(value) => setField("saleFeePercent", value)}
              />
            )}
          </Field>
          <Field label="Rounding">
            {(props) => (
              <Select
                {...props}
                value={values.rounding}
                onChange={(rounding) => setField("rounding", rounding)}
                options={ROUNDING_OPTIONS}
              />
            )}
          </Field>
          <Field
            label="Compare-at percentage"
            description="Adds a struck-through “was” price above the selling price."
          >
            {(props) => (
              <DecimalInput
                {...props}
                suffix="%"
                value={values.compareAtPercent}
                onChange={(value) => setField("compareAtPercent", value)}
              />
            )}
          </Field>
          <Field
            label="Currency"
            error={errors.currency}
            description="Leave blank to price in whatever currency the product is in. Set it and the rule will only price costs already in that currency."
          >
            {(props) => (
              <Input
                {...props}
                value={values.currency}
                maxLength={3}
                placeholder="GBP"
                onChange={(event) =>
                  setField("currency", event.target.value.toUpperCase())
                }
              />
            )}
          </Field>
          <Field label="Supplier shipping">
            {(props) => (
              <Select
                {...props}
                value={values.shippingCostHandling}
                onChange={(handling) => setField("shippingCostHandling", handling)}
                options={SHIPPING_HANDLING_OPTIONS.map(({ value, label }) => ({
                  value,
                  label,
                }))}
              />
            )}
          </Field>
        </div>
        <p className="text-xs text-muted-foreground">
          {
            SHIPPING_HANDLING_OPTIONS.find(
              (option) => option.value === values.shippingCostHandling,
            )?.hint
          }
        </p>
      </div>

      <div className="space-y-4 rounded-lg border p-4">
        <Toggle
          checked={values.appliesToNewImports}
          onChange={(checked) => setField("appliesToNewImports", checked)}
          label="Apply to new imports"
          description="Prices each product as it is imported. Existing drafts are not touched."
        />
        <Toggle
          checked={values.isActive}
          onChange={(checked) => setField("isActive", checked)}
          label="Active"
          description="Only active rules take part in resolution."
        />
      </div>

      {showNote && (
        <Field
          label="Reason for this change"
          description="Recorded against this version in the rule's history."
        >
          {(props) => (
            <Textarea
              {...props}
              rows={2}
              maxLength={500}
              value={values.note}
              onChange={(event) => setField("note", event.target.value)}
              placeholder="Raising margin for Q4"
            />
          )}
        </Field>
      )}

      <Callout>
        <p>
          Saving a rule does not reprice anything. It applies to products
          imported from now on; existing drafts are repriced from the Preview
          and Impact workflow, and published products are never repriced
          automatically.
        </p>
      </Callout>

      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy || !isDirty}>
          {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />}
          {submitLabel}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          {isDirty ? "Discard changes" : "Cancel"}
        </Button>
      </div>
    </form>
  );
}
