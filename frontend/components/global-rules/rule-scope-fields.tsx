"use client";

import { Input } from "@/components/ui/input";
import {
  Callout,
  Field,
  SCOPE_LABEL,
  SCOPE_ORDER,
  Select,
  scopeIdentifierLabel,
} from "@/components/global-rules/rule-primitives";
import {
  SCOPE_IDENTIFIER,
  type RuleScope,
  type ScopeIdentifiers,
} from "@/services/global-rules";

/**
 * Scope selector plus exactly the one identifier that scope needs.
 *
 * Showing all four identifier boxes and validating on save would let a
 * merchant fill in a product id on a store rule and only find out afterwards.
 * The backend enforces the same rule — a global rule must set none, every
 * other scope must set its own — so this mirrors it rather than replacing it.
 *
 * **Identifiers are entered, not searched.** There is no product, variant or
 * category lookup endpoint in this API, and building a second product search
 * against `/products` to feed a settings form would be a parallel system with
 * its own pagination, permissions and tenant-scoping to get right. Ids are
 * validated for shape here and for existence by the backend, and the
 * limitation is stated on screen rather than papered over. Store ids are the
 * one exception: `/stores` already lists them, so that becomes a real picker.
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

      {key === "storeId" && (
        <Field label="Store" required error={error} data-testid="scope-store">
          {(props) => (
            <Select
              {...props}
              value={value.storeId ?? ""}
              onChange={(storeId) => onChange({ ...value, storeId: storeId || null })}
              options={[
                { value: "", label: "Select a store…" },
                ...stores.map((store) => ({ value: store.id, label: store.name })),
              ]}
            />
          )}
        </Field>
      )}

      {key && key !== "storeId" && identifierLabel && (
        <Field
          label={identifierLabel}
          required
          error={error}
          description={
            key === "categoryId"
              ? "The supplier's category identifier, as recorded on imported products."
              : "Paste the identifier from the product's URL. There is no lookup here yet — see the note below."
          }
        >
          {(props) => (
            <Input
              {...props}
              value={value[key] ?? ""}
              disabled={disabled}
              placeholder={key === "categoryId" ? "380230" : "00000000-0000-0000-0000-000000000000"}
              onChange={(event) =>
                onChange({ ...value, [key]: event.target.value || null })
              }
            />
          )}
        </Field>
      )}

      {key && key !== "storeId" && (
        <Callout title="Identifiers are entered, not searched">
          <p>
            This release has no product, variant or category picker: the rules
            API offers no lookup, and building a second product search to feed
            this form would duplicate one that already exists elsewhere. Paste
            the identifier — it is checked when you save.
          </p>
        </Callout>
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
