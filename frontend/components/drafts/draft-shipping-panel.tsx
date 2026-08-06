"use client";

import { useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatMoney } from "@/lib/utils";
import { useRefreshDraft, useUpdateDraft } from "@/services/drafts";
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
  const update = useUpdateDraft(productId);
  const [requiresShipping, setRequiresShipping] = useState(
    product.requiresShipping ?? true,
  );
  const [weight, setWeight] = useState(product.packageWeightKg ?? "");
  const [length, setLength] = useState(
    product.packageLengthCm?.toString() ?? "",
  );
  const [width, setWidth] = useState(product.packageWidthCm?.toString() ?? "");
  const [height, setHeight] = useState(
    product.packageHeightCm?.toString() ?? "",
  );
  const [hsCode, setHsCode] = useState(product.hsCode ?? "");
  const [origin, setOrigin] = useState(product.countryOfOrigin ?? "");
  const [customs, setCustoms] = useState(product.customsDescription ?? "");
  const [handling, setHandling] = useState(
    product.handlingTimeDays?.toString() ?? "",
  );
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setRequiresShipping(product.requiresShipping ?? true);
    setWeight(product.packageWeightKg ?? "");
    setLength(product.packageLengthCm?.toString() ?? "");
    setWidth(product.packageWidthCm?.toString() ?? "");
    setHeight(product.packageHeightCm?.toString() ?? "");
    setHsCode(product.hsCode ?? "");
    setOrigin(product.countryOfOrigin ?? "");
    setCustoms(product.customsDescription ?? "");
    setHandling(product.handlingTimeDays?.toString() ?? "");
  }, [product]);

  async function saveMerchantShipping() {
    setError(null);
    setSaved(false);
    try {
      await update.mutateAsync({
        requiresShipping,
        packageWeightKg: weight.trim() || null,
        packageLengthCm: length ? Number(length) : null,
        packageWidthCm: width ? Number(width) : null,
        packageHeightCm: height ? Number(height) : null,
        hsCode: hsCode.trim() || null,
        countryOfOrigin: origin.trim() || null,
        customsDescription: customs.trim() || null,
        handlingTimeDays: handling ? Number(handling) : null,
        weightUnit: "kg",
        dimensionUnit: "cm",
      });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save shipping.");
    }
  }

  const shippingCost =
    product.shippingCost != null
      ? formatMoney(product.shippingCost, product.currency)
      : "Shipping cost unavailable";

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Shipping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Supplier snapshot, merchant physical/customs data, and Shopify
            delivery notes. Missing freight is never treated as $0.
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
          Refresh supplier shipping
        </Button>
      </div>

      <div>
        <h3 className="text-sm font-semibold">A. Supplier shipping</h3>
        {product.shippingCost == null ? (
          <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
            Shipping cost unavailable — profit tools must not invent free freight.
          </div>
        ) : null}
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Shipping cost" value={shippingCost} warn={product.shippingCost == null} />
          <Field
            label="Destination country"
            value={product.shipToCountry ?? "Not provided"}
            warn={!product.shipToCountry}
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
          <Field
            label="Warehouse / origin"
            value={product.warehouseOrigin ?? "Not provided"}
            warn={!product.warehouseOrigin}
          />
        </div>
      </div>

      <div className="space-y-3 rounded-lg border p-4">
        <h3 className="text-sm font-semibold">B. Product shipping data</h3>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={requiresShipping}
            onChange={(e) => setRequiresShipping(e.target.checked)}
          />
          Physical product / requires shipping
        </label>
        {requiresShipping ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label>Weight (kg)</Label>
              <Input value={weight} onChange={(e) => setWeight(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>Handling time (days)</Label>
              <Input
                value={handling}
                onChange={(e) => setHandling(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>Length (cm)</Label>
              <Input value={length} onChange={(e) => setLength(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>Width (cm)</Label>
              <Input value={width} onChange={(e) => setWidth(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>Height (cm)</Label>
              <Input value={height} onChange={(e) => setHeight(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>Country of origin</Label>
              <Input value={origin} onChange={(e) => setOrigin(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>HS tariff code</Label>
              <Input value={hsCode} onChange={(e) => setHsCode(e.target.value)} />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label>Customs description</Label>
              <Input
                value={customs}
                onChange={(e) => setCustoms(e.target.value)}
              />
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Digital/non-physical products do not require shipping dimensions.
          </p>
        )}
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={update.isPending}
            onClick={() => void saveMerchantShipping()}
          >
            {update.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : null}
            Save shipping data
          </Button>
          {saved ? (
            <span className="text-xs text-muted-foreground">Saved</span>
          ) : null}
          {error ? <span className="text-xs text-destructive">{error}</span> : null}
        </div>
      </div>

      <div className="rounded-lg border border-dashed p-4">
        <h3 className="text-sm font-semibold">C. Shopify customer shipping</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          Delivery profiles, zones, and rates require{" "}
          <code className="text-xs">read_shipping</code> /{" "}
          <code className="text-xs">write_shipping</code> and merchant
          confirmation. This release keeps least privilege: product weight,
          origin, HS code, and requires-shipping only. Profile assignment UI
          lands after scope reauthorization.
        </p>
      </div>
    </section>
  );
}
