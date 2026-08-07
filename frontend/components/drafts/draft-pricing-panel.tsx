"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatMoney } from "@/lib/utils";
import {
  useApplyDraftPricing,
  useDraftPricing,
  usePreviewDraftPricing,
} from "@/services/drafts";
import { useRefreshStoreCurrency } from "@/services/stores";
import type { DraftPricingApplyMode, ProductDetail } from "@/types/api";

interface DraftPricingPanelProps {
  productId: string;
  product: ProductDetail;
}

function money(value: string | null | undefined, currency: string | null) {
  if (value == null) return "—";
  return formatMoney(value, currency ?? undefined);
}

export function DraftPricingPanel({
  productId,
  product,
}: DraftPricingPanelProps) {
  const pricing = useDraftPricing(productId);
  const preview = usePreviewDraftPricing(productId);
  const apply = useApplyDraftPricing(productId);
  const [mode, setMode] = useState<DraftPricingApplyMode>("percentage_markup");
  const [markupPercent, setMarkupPercent] = useState("50");
  const [markupFixed, setMarkupFixed] = useState("5");
  const [targetMargin, setTargetMargin] = useState("40");
  const [sellPrice, setSellPrice] = useState("");
  const [compareAt, setCompareAt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showTechnical, setShowTechnical] = useState(false);

  const workspace = preview.data ?? pricing.data;
  const busy = preview.isPending || apply.isPending;
  const blocked = Boolean(workspace?.pricingBlocked);
  const sellingCurrency =
    workspace?.sellingCurrency ?? workspace?.currency ?? null;
  const destinationStoreId = workspace?.destinationStoreId ?? null;
  const currencyMissing =
    workspace?.pricingBlockCode === "selling_currency_missing";
  const refreshCurrency = useRefreshStoreCurrency(destinationStoreId);

  async function run(kind: "preview" | "apply") {
    setError(null);
    if (kind === "apply" && blocked) {
      setError(
        workspace?.pricingBlockMessage ??
          "Pricing cannot be calculated because a valid currency conversion is not available.",
      );
      return;
    }
    const payload = {
      mode,
      markupPercent: mode === "percentage_markup" ? markupPercent : undefined,
      markupFixed: mode === "fixed_markup" ? markupFixed : undefined,
      targetMarginPercent: mode === "target_margin" ? targetMargin : undefined,
      sellPrice: mode === "set_sell_price" ? sellPrice : undefined,
      compareAtPrice: mode === "set_compare_at" ? compareAt : undefined,
      roundToCents: true,
      includeShippingInCost: true,
      psychologicalRounding: false,
    };
    try {
      if (kind === "preview") {
        await preview.mutateAsync(payload);
      } else {
        await apply.mutateAsync(payload);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Pricing action failed.");
    }
  }

  async function onRefreshCurrency() {
    setError(null);
    try {
      await refreshCurrency.mutateAsync();
      await pricing.refetch();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not refresh Shopify selling currency.",
      );
    }
  }

  if (pricing.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading pricing workspace…
      </div>
    );
  }

  if (pricing.isError || !workspace) {
    return (
      <p className="text-sm text-destructive">
        Could not load pricing workspace.
      </p>
    );
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Pricing</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Destination selling currency drives all calculated columns. Supplier
            costs keep their source currency until a valid conversion (or a
            direct target-currency price) is available.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge variant="outline" data-testid="selling-currency-badge">
            {sellingCurrency ?? "—"}
          </Badge>
          {workspace.sellingCurrencySource ? (
            <span className="text-[11px] text-muted-foreground">
              Source: {workspace.sellingCurrencySource}
            </span>
          ) : null}
          {workspace.fxIsStale ? (
            <span className="text-[11px] text-amber-700">FX rate is stale</span>
          ) : null}
        </div>
      </div>

      {blocked ? (
        <div
          className="space-y-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-3 text-sm"
          role="alert"
          data-testid="pricing-blocked-banner"
        >
          <p className="font-medium text-destructive">
            {workspace.pricingBlockMessage ??
              "Pricing cannot be calculated because a valid currency conversion is not available."}
          </p>
          <p className="text-muted-foreground">
            Calculated profit and proposed prices are hidden until currencies
            align or a valid exchange rate is available. Source amounts are not
            relabelled.
          </p>
          <div className="flex flex-wrap gap-2">
            {currencyMissing && destinationStoreId ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={refreshCurrency.isPending}
                onClick={() => void onRefreshCurrency()}
              >
                {refreshCurrency.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : null}
                Refresh store currency
              </Button>
            ) : (
              <Button type="button" size="sm" variant="outline" disabled>
                Refresh exchange rate
              </Button>
            )}
            <Button type="button" size="sm" variant="outline" asChild>
              <a href="/settings/integrations">Review market settings</a>
            </Button>
            <Button type="button" size="sm" variant="outline" asChild>
              <a href="/stores">Select destination store</a>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setShowTechnical((v) => !v)}
            >
              {showTechnical ? "Hide" : "View"} technical details
            </Button>
          </div>
          {showTechnical ? (
            <dl className="grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
              <div>
                <dt className="font-medium text-foreground">Block code</dt>
                <dd>{workspace.pricingBlockCode ?? "fx_unavailable"}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">FX provider</dt>
                <dd>{workspace.fxProvider ?? "—"}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">FX status</dt>
                <dd>{workspace.fxStatus ?? "—"}</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Product</dt>
                <dd className="font-mono">{productId}</dd>
              </div>
            </dl>
          ) : null}
        </div>
      ) : null}

      {!workspace.shippingCostAvailable ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
          {workspace.shippingWarning ??
            "Shipping cost unavailable — freight is not treated as zero."}
        </div>
      ) : null}

      {!blocked ? (
        <p className="text-xs text-muted-foreground">{workspace.fxNote}</p>
      ) : null}

      <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
        <label className="space-y-1 text-xs">
          <span className="text-muted-foreground">Mode</span>
          <select
            className="flex h-9 w-48 rounded-md border bg-background px-2 text-sm"
            value={mode}
            onChange={(event) =>
              setMode(event.target.value as DraftPricingApplyMode)
            }
          >
            <option value="percentage_markup">Percentage markup</option>
            <option value="fixed_markup">Fixed markup</option>
            <option value="target_margin">Target margin</option>
            <option value="set_sell_price">Set selling price</option>
            <option value="set_compare_at">Set compare-at</option>
          </select>
        </label>
        {mode === "percentage_markup" ? (
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">Markup %</span>
            <Input
              value={markupPercent}
              onChange={(e) => setMarkupPercent(e.target.value)}
              className="h-9 w-28"
            />
          </label>
        ) : null}
        {mode === "fixed_markup" ? (
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">Markup amount</span>
            <Input
              value={markupFixed}
              onChange={(e) => setMarkupFixed(e.target.value)}
              className="h-9 w-28"
            />
          </label>
        ) : null}
        {mode === "target_margin" ? (
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">Target margin %</span>
            <Input
              value={targetMargin}
              onChange={(e) => setTargetMargin(e.target.value)}
              className="h-9 w-28"
            />
          </label>
        ) : null}
        {mode === "set_sell_price" ? (
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">Sell price</span>
            <Input
              value={sellPrice}
              onChange={(e) => setSellPrice(e.target.value)}
              className="h-9 w-28"
            />
          </label>
        ) : null}
        {mode === "set_compare_at" ? (
          <label className="space-y-1 text-xs">
            <span className="text-muted-foreground">Compare-at</span>
            <Input
              value={compareAt}
              onChange={(e) => setCompareAt(e.target.value)}
              className="h-9 w-28"
            />
          </label>
        ) : null}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy || blocked}
          onClick={() => void run("preview")}
        >
          Preview
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy || blocked}
          onClick={() => void run("apply")}
        >
          Apply to enabled variants
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Variant</TableHead>
              <TableHead>Supplier source</TableHead>
              <TableHead>Localized / converted</TableHead>
              <TableHead>Sell ({sellingCurrency ?? "—"})</TableHead>
              <TableHead>Proposed</TableHead>
              <TableHead>Profit</TableHead>
              <TableHead>Margin</TableHead>
              <TableHead>Break-even</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workspace.variants.map((row) => (
              <TableRow key={row.variantId}>
                <TableCell className="max-w-[14rem] truncate text-sm">
                  {row.label ?? "Variant"}
                  {!row.isEnabled ? (
                    <Badge className="ml-2" variant="secondary">
                      Disabled
                    </Badge>
                  ) : null}
                  {row.rowBlocked ? (
                    <Badge className="ml-2" variant="destructive">
                      Blocked
                    </Badge>
                  ) : null}
                </TableCell>
                <TableCell>
                  {money(row.supplierCost, row.supplierCurrency)}
                </TableCell>
                <TableCell>
                  {row.rowBlocked
                    ? "—"
                    : money(
                        row.convertedCost,
                        row.convertedCurrency ?? sellingCurrency,
                      )}
                </TableCell>
                <TableCell>{money(row.sellPrice, sellingCurrency)}</TableCell>
                <TableCell>
                  {money(row.proposedSellPrice, sellingCurrency)}
                </TableCell>
                <TableCell>
                  {row.rowBlocked ? "—" : money(row.profit, sellingCurrency)}
                </TableCell>
                <TableCell>
                  {row.rowBlocked || row.marginPercent == null
                    ? "—"
                    : `${row.marginPercent}%`}
                </TableCell>
                <TableCell>
                  {row.rowBlocked
                    ? "—"
                    : money(row.breakEvenPrice, sellingCurrency)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="sr-only">
        Product context {product.title}. Supplier destination{" "}
        {product.shipToCountry ?? "unknown"}.
      </p>
    </section>
  );
}
