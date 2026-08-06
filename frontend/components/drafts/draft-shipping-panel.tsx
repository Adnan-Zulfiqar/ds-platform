"use client";

import { Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { countryName } from "@/lib/countries";
import { formatMoney } from "@/lib/utils";
import { useRefreshDraft } from "@/services/drafts";
import type { ProductDetail } from "@/types/api";

interface DraftShippingPanelProps {
  productId: string;
  product: ProductDetail;
}

function Field({
  label,
  value,
  warn,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={
          warn
            ? "mt-1 text-sm font-medium text-amber-700 dark:text-amber-400"
            : "mt-1 text-sm font-medium"
        }
      >
        {value}
      </p>
    </div>
  );
}

export function DraftShippingPanel({
  productId,
  product,
}: DraftShippingPanelProps) {
  const refresh = useRefreshDraft(productId);
  const dims =
    product.packageLengthCm != null &&
    product.packageWidthCm != null &&
    product.packageHeightCm != null
      ? `${product.packageLengthCm} × ${product.packageWidthCm} × ${product.packageHeightCm} cm`
      : "Missing — refresh supplier data";
  const shippingCost =
    product.shippingCost != null
      ? formatMoney(product.shippingCost, product.currency ?? undefined)
      : "Shipping cost unavailable";

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Shipping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Package and logistics snapshot from the supplier product payload.
            Missing freight is shown explicitly — never as $0. Availability is
            destination-specific — do not assume another country ships the same.
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
          Refresh for this destination
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Imported for"
          value={
            product.importShipToCountry
              ? countryName(product.importShipToCountry)
              : "Not recorded"
          }
          warn={product.importShipToCountry == null}
        />
        <Field
          label="Last checked"
          value={
            product.importShipToCheckedAt
              ? new Date(product.importShipToCheckedAt).toLocaleString()
              : "Not recorded"
          }
          warn={product.importShipToCheckedAt == null}
        />
      </div>

      {product.shippingCost == null ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
          Shipping cost unavailable. Publishing should warn or block according
          to store policy — do not assume free shipping.
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Destination country"
          value={product.shipToCountry ?? "Not provided"}
          warn={product.shipToCountry == null}
        />
        <Field
          label="Estimated delivery"
          value={
            product.deliveryTimeDays != null
              ? `${product.deliveryTimeDays} days`
              : "Not provided"
          }
          warn={product.deliveryTimeDays == null}
        />
        <Field label="Shipping cost" value={shippingCost} warn={product.shippingCost == null} />
        <Field
          label="Warehouse / origin"
          value={product.warehouseOrigin ?? "Not provided"}
          warn={product.warehouseOrigin == null}
        />
        <Field
          label="Package weight"
          value={
            product.packageWeightKg != null
              ? `${product.packageWeightKg} kg`
              : "Missing"
          }
          warn={product.packageWeightKg == null}
        />
        <Field label="Package dimensions" value={dims} warn={dims.startsWith("Missing")} />
      </div>
    </section>
  );
}
