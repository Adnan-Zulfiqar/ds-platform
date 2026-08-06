"use client";

import { Loader2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useRefreshDraft } from "@/services/drafts";
import type { ProductDetail } from "@/types/api";

interface DraftInventoryPanelProps {
  productId: string;
  product: ProductDetail;
}

function freshnessLabel(lastSyncedAt: string | null): {
  label: string;
  stale: boolean;
} {
  if (!lastSyncedAt) {
    return { label: "Never refreshed from supplier", stale: true };
  }
  const ageMs = Date.now() - new Date(lastSyncedAt).getTime();
  const hours = ageMs / (1000 * 60 * 60);
  if (hours > 24) {
    return {
      label: `Stale — last supplier refresh ${Math.floor(hours)}h ago`,
      stale: true,
    };
  }
  return {
    label: `Fresh — last supplier refresh ${new Date(lastSyncedAt).toLocaleString()}`,
    stale: false,
  };
}

export function DraftInventoryPanel({
  productId,
  product,
}: DraftInventoryPanelProps) {
  const refresh = useRefreshDraft(productId);
  const freshness = freshnessLabel(product.lastSyncedAt);
  const variantTotal = product.variants.reduce(
    (sum, variant) => sum + variant.stockQuantity,
    0,
  );

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Inventory</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Stock values below are{" "}
            <strong className="font-medium text-foreground">
              supplier-available quantity
            </strong>{" "}
            as of the last AliExpress refresh — not reserved DropPilot stock or
            Shopify on-hand after publication.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate()}
        >
          {refresh.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" />
          )}
          Refresh supplier stock
        </Button>
      </div>

      <div
        className={
          freshness.stale
            ? "rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm"
            : "rounded-md border px-3 py-2 text-sm text-muted-foreground"
        }
      >
        {freshness.label}
        {product.lastSyncError ? (
          <span className="mt-1 block text-destructive">
            Last sync error: {product.lastSyncError}
          </span>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">Supplier total (cached)</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {product.stockQuantity.toLocaleString()}
          </p>
        </div>
        <div className="rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">Sum of variant SKUs</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">
            {variantTotal.toLocaleString()}
          </p>
        </div>
        <div className="rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">Shopify / reserved</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Available after publication — not tracked on drafts yet.
          </p>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Variant</TableHead>
              <TableHead>Supplier stock</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {product.variants.map((variant) => (
              <TableRow key={variant.id}>
                <TableCell className="max-w-[16rem] truncate text-sm">
                  {variant.label ?? variant.externalVariantId}
                </TableCell>
                <TableCell className="tabular-nums">
                  {variant.stockQuantity.toLocaleString()}
                </TableCell>
                <TableCell>
                  {variant.isEnabled ? (
                    <Badge variant="outline">Enabled</Badge>
                  ) : (
                    <Badge variant="secondary">Disabled</Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
