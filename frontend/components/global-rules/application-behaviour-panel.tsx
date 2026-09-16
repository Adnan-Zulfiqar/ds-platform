"use client";

import { Loader2, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { Callout, StatusBadge, Toggle } from "@/components/global-rules/rule-primitives";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError } from "@/lib/api-client";
import {
  useUpdatePricingRule,
  type PricingRule,
} from "@/services/global-rules";
import { pricingFormToPayload, pricingRuleToForm } from "@/components/global-rules/pricing-rule-form";

/**
 * How rules behave once configured, and the one setting that governs it.
 *
 * Everything on this panel is backed by a real persisted field. There is
 * deliberately no frontend-only preference here: a toggle the backend cannot
 * store would appear to work, survive until reload, and then quietly revert —
 * which is worse than not offering it.
 *
 * The only writable control is `appliesToNewImports`, which lives on each
 * pricing rule. It is surfaced here as well as inside the rule form because
 * "does anything happen automatically" is a workspace-level question a
 * merchant asks without wanting to open a rule.
 */

function AppliesToImportsRow({
  rule,
  canManage,
}: {
  rule: PricingRule;
  canManage: boolean;
}) {
  const update = useUpdatePricingRule(rule.id);
  const [error, setError] = useState<string | null>(null);

  async function toggle(checked: boolean) {
    setError(null);
    const form = pricingRuleToForm(rule);
    try {
      await update.mutateAsync({
        ...pricingFormToPayload({ ...form, appliesToNewImports: checked }),
        // The token the server last gave us, echoed back verbatim. A rule
        // edited elsewhere since this page loaded produces a 409 rather than
        // silently discarding that edit.
        expectedUpdatedAt: rule.updatedAt,
        note: checked
          ? "Enabled automatic pricing for new imports"
          : "Disabled automatic pricing for new imports",
      });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "This rule changed while the page was open. Reload to see the latest version, then try again."
          : err instanceof ApiError
            ? err.message
            : "The change could not be saved.",
      );
    }
  }

  return (
    <li className="rounded-lg border p-4" data-testid="behaviour-rule">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge active={rule.isActive} />
            <span className="font-medium">{rule.name}</span>
            <Badge variant="outline">Version {rule.version}</Badge>
          </div>
          {!rule.isActive && (
            <p className="text-xs text-muted-foreground">
              Inactive rules take no part in resolution, whatever this is set to.
            </p>
          )}
        </div>
        <div className="sm:shrink-0">
          {canManage ? (
            <div className="flex items-center gap-2">
              <Toggle
                checked={rule.appliesToNewImports}
                disabled={update.isPending}
                onChange={(checked) => void toggle(checked)}
                label="Apply to new imports"
              />
              {update.isPending && (
                <Loader2
                  className="h-4 w-4 animate-spin text-muted-foreground"
                  aria-hidden="true"
                />
              )}
            </div>
          ) : (
            <Badge variant="outline">
              {rule.appliesToNewImports
                ? "Applies to new imports"
                : "Not applied to new imports"}
            </Badge>
          )}
        </div>
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </li>
  );
}

export function ApplicationBehaviourPanel({
  rules,
  canManage,
}: {
  rules: PricingRule[];
  canManage: boolean;
}) {
  return (
    <div className="space-y-6">
      <Callout title="What happens automatically, and what does not">
        <ul className="list-disc space-y-1.5 pl-4">
          <li>
            <strong>Saving a rule changes no existing product.</strong> It takes
            effect on products imported from that point on.
          </li>
          <li>
            <strong>Published products are never repriced automatically.</strong>{" "}
            A product that is already on Shopify is skipped by every automatic
            path, and is skipped again if it is submitted to a bulk application.
          </li>
          <li>
            <strong>Existing drafts are repriced only on request</strong>, from
            the Preview and Impact workflow — which is not in this release. Until
            then, rules apply at import.
          </li>
        </ul>
      </Callout>

      <Callout title="When the numbers are not trustworthy, nothing is priced">
        <p>
          The engine fails closed. If the supplier reports no item cost, no
          freight cost or no currency — or if a rule is denominated in a
          currency the product is not in — no price is produced. The draft is
          created and marked <strong>Needs review</strong> with the specific
          reason, rather than being priced from a number nobody has.
        </p>
        <p className="mt-2">
          A zero is a number, and a wrong one, on exactly the cheap heavy items
          where shipping decides whether a sale makes money.
        </p>
      </Callout>

      <section className="space-y-3" aria-labelledby="behaviour-rules-heading">
        <div>
          <h3 id="behaviour-rules-heading" className="text-sm font-medium">
            Automatic pricing at import
          </h3>
          <p className="text-sm text-muted-foreground">
            Which pricing rules price a product as it is imported.
          </p>
        </div>

        {rules.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No pricing rules yet"
            description="Imports keep their supplier price until a pricing rule exists."
          />
        ) : (
          <ul className="space-y-3">
            {rules.map((rule) => (
              <AppliesToImportsRow key={rule.id} rule={rule} canManage={canManage} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
