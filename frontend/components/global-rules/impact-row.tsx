"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { trimDecimal } from "@/components/global-rules/rule-primitives";
import { cn } from "@/lib/utils";
import type { ImpactItem } from "@/services/global-rules";

/**
 * One draft's impact, as a card at every width.
 *
 * A row carries fifteen figures plus a rule, a scope and a set of reasons. No
 * arrangement of that as a table survives 375px, and the usual answer —
 * horizontal scroll — hides exactly the columns a merchant is checking. Cards
 * read the same everywhere.
 *
 * **Missing means missing.** A figure the backend could not produce renders as
 * "Unavailable", never as a zero. Zero is a number, and a wrong one: a product
 * shown with £0.00 shipping looks profitable precisely when it is not.
 */

export const REVIEW_REASON_COPY: Record<string, string> = {
  supplier_cost_unknown: "The supplier did not report an item cost.",
  shipping_cost_unknown:
    "The supplier did not report a freight cost, so no price can be calculated.",
  supplier_currency_unknown: "This product has no currency recorded.",
  fx_rate_unavailable:
    "The governing rule is in a different currency and no exchange rate is available.",
  no_shipping_quotes_available: "The supplier offered no priced shipping option.",
  no_shipping_method_matches_rule: "No shipping option satisfied the shipping rule.",
  no_shipping_method_matches: "No shipping option satisfied the shipping rule.",
  shipping_rule_not_satisfied_fallback_used:
    "No option met the shipping rule, so the cheapest was used.",
  shipping_destination_unknown: "No destination country is set for this product.",
  pricing_calculation_failed: "The governing rule could not be evaluated.",
};

export function explainReason(reason: string): string {
  return REVIEW_REASON_COPY[reason] ?? reason;
}

function Figure({
  label,
  value,
  currency,
  emphasis,
  testId,
}: {
  label: string;
  value: string | null;
  currency?: string | null;
  emphasis?: boolean;
  testId?: string;
}) {
  const rendered =
    value === null
      ? "Unavailable"
      : `${currency ? `${currency} ` : ""}${trimDecimal(value, 2)}`;
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        data-testid={testId}
        className={cn(
          "truncate font-mono tabular-nums",
          emphasis ? "text-sm font-semibold" : "text-sm",
          value === null && "font-sans text-xs italic text-muted-foreground",
        )}
      >
        {rendered}
      </dd>
    </div>
  );
}

export function ImpactRow({
  item,
  selected,
  selectable,
  onToggle,
}: {
  item: ImpactItem;
  selected: boolean;
  selectable: boolean;
  onToggle: (productId: string, next: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const currency = item.currency;

  return (
    <li
      data-testid="impact-row"
      data-product-id={item.productId}
      data-can-apply={item.canApply}
      data-published={item.published}
      className={cn("rounded-lg border p-4", selected && "border-primary bg-accent/30")}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          disabled={!selectable}
          onChange={(event) => onToggle(item.productId, event.target.checked)}
          // Named for a screen reader, which otherwise announces a bare
          // checkbox with no idea which product it belongs to.
          aria-label={
            selectable
              ? `Select ${item.title}`
              : `${item.title} cannot be selected`
          }
          data-testid="impact-select"
          className="mt-1 h-5 w-5 shrink-0 cursor-pointer rounded border-input accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-40"
        />

        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{item.title}</span>
            {item.published && (
              <Badge variant="outline" data-testid="published-badge">
                Published
              </Badge>
            )}
            {item.needsReview && !item.published && (
              <Badge variant="outline" data-testid="review-badge">
                Needs review
              </Badge>
            )}
            {item.canApply && (
              <Badge variant="secondary" data-testid="safe-badge">
                Ready
              </Badge>
            )}
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-4">
            <Figure label="Current price" value={item.currentPrice} currency={currency} />
            <Figure
              label="Proposed price"
              value={item.proposedPrice}
              currency={currency}
              emphasis
              testId="impact-proposed"
            />
            <Figure label="Item cost" value={item.itemCost} currency={currency} />
            <Figure
              label="Supplier shipping"
              value={item.supplierShippingCost}
              currency={currency}
              testId="impact-shipping"
            />
            <Figure label="Duty and fees" value={item.fees} currency={currency} />
            <Figure label="Landed cost" value={item.landedCost} currency={currency} />
            <Figure label="Profit" value={item.profit} currency={currency} />
            <Figure
              label="Markup"
              value={item.markupPercent === null ? null : `${item.markupPercent}`}
            />
          </dl>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>
              Rule:{" "}
              <span className="text-foreground">
                {item.pricingRuleVersion === null
                  ? "none matches"
                  : `${item.pricingRuleScope ?? "?"} · version ${item.pricingRuleVersion}`}
              </span>
            </span>
            {item.shippingRuleVersion !== null && (
              <span>
                Shipping rule:{" "}
                <span className="text-foreground">version {item.shippingRuleVersion}</span>
              </span>
            )}
          </div>

          {(item.needsReview || item.published) && (
            <div
              className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs"
              data-testid="impact-reasons"
            >
              {item.published ? (
                <p>
                  This product has a live channel listing. Published products
                  are never repriced by this workflow — changing a live price
                  needs a separate publish step, which is not part of this
                  release.
                </p>
              ) : (
                <ul className="list-disc space-y-1 pl-4">
                  {item.reviewReasons.map((reason) => (
                    <li key={reason}>{explainReason(reason)}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {item.variants.length > 0 && (
            <div>
              <button
                type="button"
                aria-expanded={open}
                onClick={() => setOpen((current) => !current)}
                className="flex min-h-10 items-center gap-1 rounded text-xs font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {open ? (
                  <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                {item.variants.length} variant{item.variants.length === 1 ? "" : "s"}
              </button>
              {open && (
                <ul className="mt-2 space-y-2">
                  {item.variants.map((variant) => (
                    <li
                      key={variant.variantId}
                      className="rounded border p-2"
                      data-testid="impact-variant"
                    >
                      <p className="text-sm font-medium">
                        {variant.label ?? "Unnamed variant"}
                      </p>
                      <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
                        <Figure
                          label="Current"
                          value={variant.currentPrice}
                          currency={currency}
                        />
                        <Figure
                          label="Proposed"
                          value={variant.proposedPrice}
                          currency={currency}
                        />
                        <Figure
                          label="Landed"
                          value={variant.landedCost}
                          currency={currency}
                        />
                        <Figure label="Profit" value={variant.profit} currency={currency} />
                      </dl>
                      {variant.reviewReasons.length > 0 && (
                        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                          {variant.reviewReasons.map((reason) => (
                            <li key={reason}>{explainReason(reason)}</li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
