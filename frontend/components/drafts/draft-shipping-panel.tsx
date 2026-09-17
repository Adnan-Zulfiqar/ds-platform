"use client";

import { useEffect, useState } from "react";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";

import {
  formatRelativeCheckedAt,
  shipToLabel,
} from "@/components/drafts/editor-header/readiness";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { countryName } from "@/lib/countries";
import { formatMoney } from "@/lib/utils";
import { useRefreshDraft, useUpdateDraft } from "@/services/drafts";
import type { ProductDetail } from "@/types/api";

interface DraftShippingPanelProps {
  productId: string;
  product: ProductDetail;
}

function MetricRow({
  label,
  value,
  detail,
  warn,
}: {
  label: string;
  value: string;
  detail?: string;
  warn?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-right">
        <p
          className={
            warn
              ? "text-sm font-medium text-amber-800 dark:text-amber-300"
              : "text-sm font-medium text-foreground"
          }
        >
          {value}
        </p>
        {detail ? (
          <p className="text-xs text-muted-foreground">{detail}</p>
        ) : null}
      </dd>
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
  const [detailsOpen, setDetailsOpen] = useState(false);

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

  const destinationCode = product.shipToCountry ?? product.importShipToCountry;
  const destinationName = destinationCode
    ? countryName(destinationCode)
    : "Not recorded";
  const shippingAvailable = product.shippingCost != null;
  const lastChecked = formatRelativeCheckedAt(
    product.importShipToCheckedAt ?? product.lastSyncedAt,
  );

  return (
    <section className="space-y-8" data-testid="draft-shipping-panel">
      <div>
        <h2 className="text-lg font-semibold">Shipping</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Can this product be shipped safely to the selected destination?
        </p>
      </div>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Shipping destination</h3>
        <dl className="divide-y divide-border/70">
          <MetricRow
            label="Country"
            value={destinationName}
            warn={!destinationCode}
          />
        </dl>
      </section>

      <section className="space-y-3" data-testid="shipping-price-block">
        <h3 className="text-sm font-semibold">Shipping price</h3>
        {shippingAvailable ? (
          <p
            className="text-2xl font-semibold tabular-nums"
            data-testid="shipping-price-value"
          >
            {formatMoney(product.shippingCost, product.currency)}
          </p>
        ) : (
          <div
            className="rounded-[10px] border border-amber-500/30 bg-amber-500/5 p-4"
            data-testid="shipping-unavailable"
          >
            <p className="font-medium text-amber-900 dark:text-amber-200">
              Shipping price not available
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              We couldn’t get a current shipping price from the supplier. Check
              again before confirming your selling price.
            </p>
            <p className="mt-2 text-sm text-muted-foreground">
              DropPilot will not treat missing shipping as free.
            </p>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
            data-testid="shipping-refresh"
          >
            {refresh.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            {refresh.isPending ? "Checking shipping…" : "Check shipping again"}
          </Button>
          {product.externalUrl ? (
            <Button type="button" variant="outline" size="sm" asChild>
              <a
                href={product.externalUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink className="mr-2 h-4 w-4" aria-hidden="true" />
                Open supplier product
              </a>
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setDetailsOpen((open) => !open)}
          >
            {detailsOpen ? "Hide details" : "View details"}
          </Button>
        </div>
        {refresh.isError ? (
          <p className="text-sm text-destructive" role="alert">
            We couldn’t refresh supplier shipping. Your draft is unchanged. Try
            again in a moment.
          </p>
        ) : null}
        {detailsOpen ? (
          <p className="text-xs text-muted-foreground">
            Destination is specific to this product. A missing price is never
            stored as zero.
            {product.lastSyncError
              ? ` Last supplier note: ${product.lastSyncError}`
              : ""}
          </p>
        ) : null}
      </section>

      <section>
        <h3 className="text-sm font-semibold">Delivery information</h3>
        <dl className="mt-2 divide-y divide-border/70">
          <MetricRow
            label="Ships from"
            value={
              product.warehouseOrigin
                ? shipToLabel(product.warehouseOrigin) ??
                  countryName(product.warehouseOrigin)
                : "Not provided"
            }
            warn={!product.warehouseOrigin}
          />
          <MetricRow
            label="Estimated delivery"
            value={
              product.deliveryTimeDays != null
                ? `${product.deliveryTimeDays} days`
                : "Not provided"
            }
            warn={product.deliveryTimeDays == null}
          />
          <MetricRow
            label="Last checked"
            value={lastChecked?.relative ?? "Not recorded"}
            detail={lastChecked?.exact}
            warn={!lastChecked}
          />
        </dl>
      </section>

      <section className="space-y-3 rounded-[10px] border border-border/80 bg-card p-4">
        <div>
          <h3 className="text-sm font-semibold">Your shipping settings</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Used when you publish. These do not change the supplier’s quoted
            freight.
          </p>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4"
            checked={requiresShipping}
            onChange={(e) => setRequiresShipping(e.target.checked)}
          />
          This product needs shipping
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
              <Label>Ships from (origin country)</Label>
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
            Digital products do not need package size or weight.
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
            Save shipping settings
          </Button>
          {saved ? (
            <span className="text-xs text-muted-foreground">Saved in DropPilot</span>
          ) : null}
          {error ? (
            <span className="text-xs text-destructive" role="alert">
              {error}
            </span>
          ) : null}
        </div>
      </section>
    </section>
  );
}
