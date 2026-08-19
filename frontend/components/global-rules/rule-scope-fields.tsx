"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import {
  Callout,
  Field,
  SCOPE_LABEL,
  SCOPE_ORDER,
  Select,
  scopeIdentifierLabel,
} from "@/components/global-rules/rule-primitives";
import { TargetCombobox } from "@/components/global-rules/target-combobox";
import {
  SCOPE_IDENTIFIER,
  type RuleScope,
  type ScopeIdentifiers,
  type TargetKind,
} from "@/services/global-rules";

/**
 * Scope selector plus exactly the one identifier that scope needs.
 *
 * Showing all four identifier boxes and validating on save would let a
 * merchant fill in a product id on a store rule and only find out afterwards.
 * The backend enforces the same rule — a global rule must set none, every
 * other scope must set its own — so this mirrors it rather than replacing it.
 *
 * **Targets are chosen by name.** Each scope gets a searchable combobox over
 * `/global-rules/targets/{kind}`, which returns labels and ids and nothing
 * else. The merchant picks a product; the form stores its identifier.
 *
 * Raw identifier entry survives as an explicitly-labelled advanced fallback,
 * for the cases the search cannot serve -- an id copied from a support ticket,
 * or a record the picker cannot reach. It is collapsed by default, because
 * offering a UUID box beside a search box invites people to use the wrong one.
 */

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string): boolean {
  return UUID_RE.test(value.trim());
}

export interface ScopeValue extends ScopeIdentifiers {
  scope: RuleScope;
  priority: number;
}

/** Client-side scope validation, mirroring the backend's own rule. */
export function validateScope(value: ScopeValue): string | null {
  const key = SCOPE_IDENTIFIER[value.scope];
  if (!key) {
    const stray = (["storeId", "categoryId", "productId", "variantId"] as const).find(
      (field) => value[field],
    );
    return stray ? "A global rule must not target a specific store, category, product or variant." : null;
  }
  const raw = (value[key] ?? "").trim();
  if (!raw) {
    return `A ${value.scope} rule needs its ${scopeIdentifierLabel(value.scope)}.`;
  }
  if (key !== "categoryId" && !isUuid(raw)) {
    return `${scopeIdentifierLabel(value.scope)} must be a valid identifier (UUID).`;
  }
  return null;
}

/** Clear identifiers that no longer belong to the selected scope. */
export function withScope(value: ScopeValue, scope: RuleScope): ScopeValue {
  const key = SCOPE_IDENTIFIER[scope];
  return {
    ...value,
    scope,
    storeId: key === "storeId" ? value.storeId : null,
    categoryId: key === "categoryId" ? value.categoryId : null,
    productId: key === "productId" ? value.productId : null,
    variantId: key === "variantId" ? value.variantId : null,
  };
}

interface StoreOption {
  id: string;
  name: string;
}

const SCOPE_TARGET_KIND: Partial<Record<RuleScope, TargetKind>> = {
  store: "store",
  category: "category",
  product: "product",
  variant: "variant",
};

export function RuleScopeFields({
  value,
  onChange,
  stores,
  error,
  disabled,
}: {
  value: ScopeValue;
  onChange: (next: ScopeValue) => void;
  stores: StoreOption[];
  error?: string;
  disabled?: boolean;
}) {
  const key = SCOPE_IDENTIFIER[value.scope];
  const identifierLabel = scopeIdentifierLabel(value.scope);
  const targetKind = SCOPE_TARGET_KIND[value.scope];
  const [advanced, setAdvanced] = useState(false);
  // Held so an edit form shows the name of what is already selected. A store
  // can be named from the list already loaded; the other kinds show their
  // label once picked, and an id with no known label still appears in the
  // advanced box, so nothing is ever invisible.
  const [chosenLabel, setChosenLabel] = useState<string | null>(null);
  const storeLabel =
    key === "storeId" && value.storeId
      ? (stores.find((store) => store.id === value.storeId)?.name ?? null)
      : null;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Scope"
          description="The narrowest matching scope wins, regardless of priority."
        >
          {(props) => (
            <Select
              {...props}
              value={value.scope}
              onChange={(scope) => onChange(withScope(value, scope))}
              options={SCOPE_ORDER.map((scope) => ({
                value: scope,
                label: SCOPE_LABEL[scope],
              }))}
            />
          )}
        </Field>

        <Field
          label="Priority"
          description="Breaks ties between rules at the same scope. Higher wins."
        >
          {(props) => (
            <Input
              {...props}
              type="number"
              min={0}
              max={10000}
              value={value.priority}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...value, priority: Number(event.target.value) || 0 })
              }
            />
          )}
        </Field>
      </div>

      {key && targetKind && identifierLabel && (
        <>
          <Field
            label={identifierLabel.replace(" ID", "")}
            required
            error={error}
            description="Start typing to search. The name is shown; the identifier is what gets saved."
          >
            {(props) => (
              <TargetCombobox
                {...props}
                kind={targetKind}
                value={value[key] ?? null}
                label={storeLabel ?? chosenLabel}
                productId={value.scope === "variant" ? value.productId : null}
                onChange={(target) => {
                  setChosenLabel(target?.label ?? null);
                  onChange({ ...value, [key]: target?.id ?? null });
                }}
              />
            )}
          </Field>

          <div>
            <button
              type="button"
              aria-expanded={advanced}
              onClick={() => setAdvanced((current) => !current)}
              className="min-h-10 rounded text-xs font-medium text-muted-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {advanced ? "Hide identifier entry" : "Enter an identifier instead"}
            </button>
            {advanced && (
              <div className="mt-2">
                <Field
                  label={`${identifierLabel} (advanced)`}
                  description="For an identifier copied from elsewhere. The search above is the normal way to do this."
                >
                  {(props) => (
                    <Input
                      {...props}
                      value={value[key] ?? ""}
                      disabled={disabled}
                      placeholder={
                        key === "categoryId"
                          ? "380230"
                          : "00000000-0000-0000-0000-000000000000"
                      }
                      onChange={(event) => {
                        setChosenLabel(null);
                        onChange({ ...value, [key]: event.target.value || null });
                      }}
                    />
                  )}
                </Field>
              </div>
            )}
          </div>
        </>
      )}

      {value.scope === "global" && (
        <Callout>
          <p>
            A global rule applies to everything with no narrower rule. It cannot
            target a specific store, category, product or variant.
          </p>
        </Callout>
      )}
    </div>
  );
}

/**
 * The precedence explainer.
 *
 * Rendered as an ordered list rather than prose because "which rule wins" is
 * the question merchants ask most about a hierarchy, and a list is scannable
 * when a paragraph is not.
 */
export function ScopePrecedenceNote() {
  return (
    <Callout title="Which rule wins">
      <p>
        The narrowest scope that matches a product wins. Priority only breaks
        ties between rules at the <em>same</em> scope.
      </p>
      <ol className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
        {SCOPE_ORDER.map((scope, index) => (
          <li key={scope} className="flex items-center gap-1.5">
            <span className="rounded bg-background px-1.5 py-0.5 font-medium text-foreground">
              {SCOPE_LABEL[scope]}
            </span>
            {index < SCOPE_ORDER.length - 1 && (
              <span aria-hidden="true" className="text-muted-foreground">
                ›
              </span>
            )}
          </li>
        ))}
      </ol>
      <p className="sr-only">
        Precedence order, narrowest first: variant, product, category, store,
        global.
      </p>
    </Callout>
  );
}
