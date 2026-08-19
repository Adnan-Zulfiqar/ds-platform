"use client";

import { Loader2 } from "lucide-react";
import { useMemo, useState } from "react";

import {
  Callout,
  DecimalInput,
  Field,
  Select,
  Toggle,
} from "@/components/global-rules/rule-primitives";
import {
  RuleScopeFields,
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
  ShippingNoMatchBehaviour,
  ShippingRule,
  ShippingSelectionStrategy,
} from "@/services/global-rules";

/**
 * Create/edit form for one shipping rule.
 *
 * A shipping rule chooses among the supplier's shipping quotes. It never
 * invents one — see the standing warning rendered at the top of this form.
 */

const STRATEGY_OPTIONS: ReadonlyArray<{
  value: ShippingSelectionStrategy;
  label: string;
  hint: string;
}> = [
  {
    value: "cheapest_tracked",
    label: "Cheapest tracked",
    hint: "Lowest cost among options that provide tracking.",
  },
  {
    value: "cheapest",
    label: "Cheapest",
    hint: "Lowest cost, tracked or not.",
  },
  {
    value: "fastest",
    label: "Fastest",
    hint: "Shortest delivery estimate, whatever it costs.",
  },
  {
    value: "fastest_under_cost",
    label: "Fastest below maximum cost",
    hint: "Shortest delivery estimate that stays within the cost ceiling.",
  },
];

const NO_MATCH_OPTIONS: ReadonlyArray<{
  value: ShippingNoMatchBehaviour;
  label: string;
  hint: string;
}> = [
  {
    value: "needs_review",
    label: "Mark for review",
    hint: "Hold the product out of automatic publishing and flag it.",
  },
  {
    value: "block_publish",
    label: "Mark for review and block publishing",
    hint: "As above, and refuse the publish call outright.",
  },
  {
    value: "cheapest_available",
    label: "Fall back to the cheapest available",
    hint: "Still flagged, so you can see your constraint was not met.",
  },
];

export interface ShippingRuleFormValues extends ScopeValue {
  name: string;
  destinationCountry: string;
  selectionStrategy: ShippingSelectionStrategy;
  maxDeliveryDays: string;
  maxShippingCost: string;
  trackingRequired: boolean;
  preferredCarriers: string;
  blockedCarriers: string;
  noMatchBehaviour: ShippingNoMatchBehaviour;
  isActive: boolean;
  note: string;
}

export function emptyShippingRule(): ShippingRuleFormValues {
  return {
    name: "",
    scope: "global",
    priority: 100,
    storeId: null,
    categoryId: null,
    productId: null,
    variantId: null,
    destinationCountry: "",
    selectionStrategy: "cheapest_tracked",
    maxDeliveryDays: "",
    maxShippingCost: "",
    trackingRequired: false,
    preferredCarriers: "",
    blockedCarriers: "",
    noMatchBehaviour: "needs_review",
    isActive: true,
    note: "",
  };
}

export function shippingRuleToForm(rule: ShippingRule): ShippingRuleFormValues {
  return {
    name: rule.name,
    scope: rule.scope,
    priority: rule.priority,
    storeId: rule.storeId,
    categoryId: rule.categoryId,
    productId: rule.productId,
    variantId: rule.variantId,
    destinationCountry: rule.destinationCountry ?? "",
    selectionStrategy: rule.selectionStrategy,
    maxDeliveryDays: rule.maxDeliveryDays?.toString() ?? "",
    maxShippingCost: rule.maxShippingCost ?? "",
    trackingRequired: rule.trackingRequired,
    preferredCarriers: rule.preferredCarriers.join(", "),
    blockedCarriers: rule.blockedCarriers.join(", "),
    noMatchBehaviour: rule.noMatchBehaviour,
    isActive: rule.isActive,
    note: "",
  };
}

export function parseCarriers(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function shippingFormToPayload(values: ShippingRuleFormValues) {
  const days = values.maxDeliveryDays.trim();
  const cost = values.maxShippingCost.trim();
  return {
    name: values.name.trim(),
    scope: values.scope,
    priority: values.priority,
    storeId: values.storeId,
    categoryId: values.categoryId,
    productId: values.productId,
    variantId: values.variantId,
    destinationCountry: values.destinationCountry.trim().toUpperCase() || null,
    selectionStrategy: values.selectionStrategy,
    maxDeliveryDays: days === "" ? null : Number(days),
    maxShippingCost: cost === "" ? null : cost,
    trackingRequired: values.trackingRequired,
    preferredCarriers: parseCarriers(values.preferredCarriers),
    blockedCarriers: parseCarriers(values.blockedCarriers),
    noMatchBehaviour: values.noMatchBehaviour,
    isActive: values.isActive,
    note: values.note.trim() || undefined,
  };
}

export function validateShippingForm(
  values: ShippingRuleFormValues,
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!values.name.trim()) errors.name = "Give the rule a name.";

  const scopeError = validateScope(values);
  if (scopeError) errors.scope = scopeError;

  const country = values.destinationCountry.trim();
  if (!country) {
    errors.destinationCountry =
      "Shipping cost depends entirely on destination, so a country is required.";
  } else if (!/^[A-Za-z]{2}$/.test(country)) {
    errors.destinationCountry = "Use a 2-letter ISO country code, for example GB.";
  }

  if (values.selectionStrategy === "fastest_under_cost" && !values.maxShippingCost.trim()) {
    errors.maxShippingCost =
      "“Fastest below maximum cost” needs a cost ceiling to stay below.";
  }

  const days = values.maxDeliveryDays.trim();
  if (days && (!/^\d+$/.test(days) || Number(days) < 1)) {
    errors.maxDeliveryDays = "Enter a whole number of days, at least 1.";
  }
  const cost = values.maxShippingCost.trim();
  if (cost && (Number.isNaN(Number(cost)) || Number(cost) < 0)) {
    errors.maxShippingCost = "Enter an amount of zero or more.";
  }

  const preferred = new Set(parseCarriers(values.preferredCarriers).map((c) => c.toLowerCase()));
  const clash = parseCarriers(values.blockedCarriers).filter((carrier) =>
    preferred.has(carrier.toLowerCase()),
  );
  if (clash.length > 0) {
    errors.blockedCarriers = `${clash.join(", ")} cannot be both preferred and blocked.`;
  }
  return errors;
}

/**
 * The standing limitation, stated wherever a shipping rule is configured.
 *
 * Without it a merchant configures a careful shipping rule, imports a product,
 * and finds it flagged for review with no explanation. The rule is working
 * correctly — there is simply nothing for it to choose between.
 */
export function MissingFreightWarning() {
  return (
    <Callout tone="warning" title="AliExpress provides no freight quote today">
      <p data-testid="missing-freight-warning">
        AliExpress imports currently provide no freight quote. Products
        requiring supplier shipping will be marked Needs review until shipping
        data becomes available.
      </p>
      <p className="mt-2">
        A shipping rule chooses between the quotes a supplier returns. It never
        assumes a cost of zero, because zero is a number — and a wrong one,
        exactly on the cheap heavy items where shipping decides whether a sale
        makes money.
      </p>
    </Callout>
  );
}

interface ShippingRuleFormProps {
  form: RuleFormState<ShippingRuleFormValues>;
  stores: Array<{ id: string; name: string }>;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
  busy: boolean;
  showNote: boolean;
}

export function ShippingRuleForm({
  form,
  stores,
  onSubmit,
  onCancel,
  submitLabel,
  busy,
  showNote,
}: ShippingRuleFormProps) {
  const { values, setField, setValues, isDirty, fieldErrors } = form;
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});
  useUnsavedWarning(isDirty);

  const errors = useMemo(
    () => ({ ...localErrors, ...fieldErrors }),
    [localErrors, fieldErrors],
  );

  const usesCostCeiling =
    values.selectionStrategy === "fastest_under_cost" ||
    values.maxShippingCost.trim() !== "";

  function submit() {
    const found = validateShippingForm(values);
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
      <MissingFreightWarning />

      <Field label="Rule name" required error={errors.name}>
        {(props) => (
          <Input
            {...props}
            value={values.name}
            maxLength={128}
            onChange={(event) => setField("name", event.target.value)}
            placeholder="UK tracked delivery"
          />
        )}
      </Field>

      <RuleScopeFields
        value={values}
        onChange={(next) => setValues((current) => ({ ...current, ...next }))}
        stores={stores}
        error={errors.scope}
      />

      <div className="space-y-4 rounded-lg border p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Destination country"
            required
            error={errors.destinationCountry}
            description="Availability and cost are entirely destination-dependent."
          >
            {(props) => (
              <Input
                {...props}
                value={values.destinationCountry}
                maxLength={2}
                placeholder="GB"
                onChange={(event) =>
                  setField("destinationCountry", event.target.value.toUpperCase())
                }
              />
            )}
          </Field>

          <Field
            label="Selection strategy"
            description={
              STRATEGY_OPTIONS.find(
                (option) => option.value === values.selectionStrategy,
              )?.hint
            }
          >
            {(props) => (
              <Select
                {...props}
                value={values.selectionStrategy}
                onChange={(strategy) => setField("selectionStrategy", strategy)}
                options={STRATEGY_OPTIONS.map(({ value, label }) => ({ value, label }))}
              />
            )}
          </Field>

          <Field label="Maximum delivery days" error={errors.maxDeliveryDays}>
            {(props) => (
              <Input
                {...props}
                inputMode="numeric"
                value={values.maxDeliveryDays}
                onChange={(event) => setField("maxDeliveryDays", event.target.value)}
                placeholder="14"
              />
            )}
          </Field>

          {usesCostCeiling && (
            <Field
              label="Maximum shipping cost"
              required={values.selectionStrategy === "fastest_under_cost"}
              error={errors.maxShippingCost}
            >
              {(props) => (
                <DecimalInput
                  {...props}
                  value={values.maxShippingCost}
                  onChange={(value) => setField("maxShippingCost", value)}
                />
              )}
            </Field>
          )}
        </div>

        <Toggle
          checked={values.trackingRequired}
          onChange={(checked) => setField("trackingRequired", checked)}
          label="Tracking required"
          description="Discard any option that does not provide tracking."
        />
      </div>

      <div className="space-y-4 rounded-lg border p-4">
        <h3 className="text-sm font-medium">Carriers</h3>
        <p className="text-xs text-muted-foreground">
          Comma-separated. Preferred carriers filter the options each strategy
          then chooses from — there is no separate “preferred carrier”
          strategy, so preferences compose with all four.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Preferred carriers" error={errors.preferredCarriers}>
            {(props) => (
              <Input
                {...props}
                value={values.preferredCarriers}
                onChange={(event) => setField("preferredCarriers", event.target.value)}
                placeholder="Royal Mail, DHL"
              />
            )}
          </Field>
          <Field label="Blocked carriers" error={errors.blockedCarriers}>
            {(props) => (
              <Input
                {...props}
                value={values.blockedCarriers}
                onChange={(event) => setField("blockedCarriers", event.target.value)}
                placeholder="Cainiao Economy"
              />
            )}
          </Field>
        </div>
      </div>

      <Field
        label="When nothing matches"
        description={
          NO_MATCH_OPTIONS.find((option) => option.value === values.noMatchBehaviour)?.hint
        }
      >
        {(props) => (
          <Select
            {...props}
            value={values.noMatchBehaviour}
            onChange={(behaviour) => setField("noMatchBehaviour", behaviour)}
            options={NO_MATCH_OPTIONS.map(({ value, label }) => ({ value, label }))}
          />
        )}
      </Field>

      <Toggle
        checked={values.isActive}
        onChange={(checked) => setField("isActive", checked)}
        label="Active"
        description="Only active rules take part in resolution."
      />

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
            />
          )}
        </Field>
      )}

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
