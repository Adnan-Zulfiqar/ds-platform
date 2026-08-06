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
  const [sellPrice, setSellPrice] = useState("");
  const [compareAt, setCompareAt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const workspace = preview.data ?? pricing.data;
  const busy = preview.isPending || apply.isPending;

  async function run(kind: "preview" | "apply") {
    setError(null);
    const payload = {
      mode,
      markupPercent: mode === "percentage_markup" ? markupPercent : undefined,
      markupFixed: mode === "fixed_markup" ? markupFixed : undefined,
      sellPrice: mode === "set_sell_price" ? sellPrice : undefined,
      compareAtPrice: mode === "set_compare_at" ? compareAt : undefined,
      roundToCents: true,
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
            Supplier cost is source data. Selling price, profit, and margin are
            calculated on the server (Decimal). Set individual prices under
            Variants, or apply bulk rules here.
          </p>
        </div>
        <Badge variant="outline">{workspace.currency ?? product.currency ?? "—"}</Badge>
      </div>

      {!workspace.shippingCostAvailable ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
          Shipping cost unavailable — freight is not treated as zero for publish
          decisions.
        </div>
      ) : null}

      <p className="text-xs text-muted-foreground">{workspace.fxNote}</p>

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
          disabled={busy}
          onClick={() => void run("preview")}
        >
          Preview
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={busy}
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
              <TableHead>Supplier cost</TableHead>
              <TableHead>Sell</TableHead>
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
                </TableCell>
                <TableCell>
                  {money(row.supplierCost, row.supplierCurrency)}
                </TableCell>
                <TableCell>{money(row.sellPrice, workspace.currency)}</TableCell>
                <TableCell>
                  {money(row.proposedSellPrice, workspace.currency)}
                </TableCell>
                <TableCell>{money(row.profit, workspace.currency)}</TableCell>
                <TableCell>
                  {row.marginPercent != null ? `${row.marginPercent}%` : "—"}
                </TableCell>
                <TableCell>
                  {money(row.breakEvenPrice, workspace.currency)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
