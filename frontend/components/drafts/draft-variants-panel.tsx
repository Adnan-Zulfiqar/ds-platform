"use client";

import { useEffect, useRef, useState } from "react";
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
import { useSetDraftVariantsEnabled, useUpdateDraftVariant } from "@/services/drafts";
import type { ProductDetail, ProductVariant } from "@/types/api";

interface DraftVariantsPanelProps {
  productId: string;
  product: ProductDetail;
}

/**
 * Parse flattened supplier labels into option rows for display.
 * Full structured rename/reorder of option axes is a later enhancement — MVP
 * edits the label string Shopify receives as option1.
 */
function parseOptions(label: string | null): { key: string; value: string }[] {
  if (!label) return [];
  return label
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [key, ...rest] = part.split(":");
      const optionKey = (key ?? "Option").trim() || "Option";
      if (rest.length === 0) return { key: "Option", value: optionKey };
      return { key: optionKey, value: rest.join(":").trim() };
    });
}

function VariantEditorRow({
  productId,
  variant,
}: {
  productId: string;
  variant: ProductVariant;
}) {
  const update = useUpdateDraftVariant(productId);
  const [label, setLabel] = useState(variant.label ?? "");
  const [merchantSku, setMerchantSku] = useState(variant.merchantSku ?? "");
  const [sellPrice, setSellPrice] = useState(variant.sellPrice ?? "");
  const [compareAt, setCompareAt] = useState(variant.compareAtPrice ?? "");
  const [enabled, setEnabled] = useState(variant.isEnabled);
  const options = parseOptions(variant.label);

  // "Enable all" / "Disable all" change every row on the server; follow it.
  // Adjusted during render (React's pattern for a prop change), so other
  // unsaved edits in the row are kept.
  const [serverEnabled, setServerEnabled] = useState(variant.isEnabled);
  if (serverEnabled !== variant.isEnabled) {
    setServerEnabled(variant.isEnabled);
    setEnabled(variant.isEnabled);
  }

  async function save() {
    await update.mutateAsync({
      variantId: variant.id,
      label: label.trim() || null,
      merchantSku: merchantSku.trim() || null,
      sellPrice: sellPrice.trim() || null,
      compareAtPrice: compareAt.trim() || null,
      isEnabled: enabled,
    });
  }

  return (
    <TableRow data-testid="draft-variant-row">
      <TableCell className="align-top">
        <div className="space-y-1">
          <Input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            aria-label="Variant label"
          />
          {options.length > 0 ? (
            <ul className="text-xs text-muted-foreground">
              {options.map((option) => (
                <li key={`${option.key}-${option.value}`}>
                  <span className="font-medium text-foreground">{option.key}</span>
                  : {option.value}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="text-[11px] text-muted-foreground">
            Supplier SKU {variant.externalVariantId}
          </p>
        </div>
      </TableCell>
      <TableCell className="align-top">
        <Input
          value={merchantSku}
          onChange={(event) => setMerchantSku(event.target.value)}
          aria-label="Merchant SKU"
          placeholder="Merchant SKU"
        />
      </TableCell>
      <TableCell className="align-top tabular-nums text-muted-foreground">
        {formatMoney(variant.costPrice, variant.currency)}
      </TableCell>
      <TableCell className="align-top">
        <Input
          value={sellPrice}
          onChange={(event) => setSellPrice(event.target.value)}
          aria-label="Selling price"
          placeholder="Sell"
          className="w-28"
        />
      </TableCell>
      <TableCell className="align-top">
        <Input
          value={compareAt}
          onChange={(event) => setCompareAt(event.target.value)}
          aria-label="Compare-at price"
          placeholder="Compare"
          className="w-28"
        />
      </TableCell>
      <TableCell className="align-top tabular-nums">
        {variant.stockQuantity.toLocaleString()}
      </TableCell>
      <TableCell className="align-top">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Enabled
        </label>
      </TableCell>
      <TableCell className="align-top">
        <Button
          type="button"
          size="sm"
          disabled={update.isPending}
          onClick={() => void save()}
        >
          {update.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            "Save"
          )}
        </Button>
      </TableCell>
    </TableRow>
  );
}

export function DraftVariantsPanel({
  productId,
  product,
}: DraftVariantsPanelProps) {
  const enabledCount = product.variants.filter((variant) => variant.isEnabled).length;
  const total = product.variants.length;
  const bulk = useSetDraftVariantsEnabled(productId);
  const allOn = total > 0 && enabledCount === total;
  const someOn = enabledCount > 0 && enabledCount < total;
  const selectAll = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (selectAll.current) selectAll.current.indeterminate = someOn;
  }, [someOn]);

  function setAll(enabled: boolean) {
    bulk.mutate({ enabled });
  }

  return (
    <section className="space-y-4" data-testid="draft-variants-panel">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Variants</h2>
          <p className="text-sm text-muted-foreground">
            Supplier cost and stock refresh from AliExpress. Selling price and
            merchant SKU are yours — sync will not overwrite them.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">
            {enabledCount}/{total} enabled
          </Badge>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={total === 0 || allOn || bulk.isPending}
            onClick={() => setAll(true)}
          >
            Enable all
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={total === 0 || enabledCount === 0 || bulk.isPending}
            onClick={() => setAll(false)}
          >
            Disable all
          </Button>
          {bulk.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-label="Saving" />
          ) : null}
        </div>
      </div>
      {bulk.isError ? (
        <p className="text-sm text-destructive" role="alert">
          The variants could not be updated. Try again.
        </p>
      ) : null}

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Options / label</TableHead>
              <TableHead>Merchant SKU</TableHead>
              <TableHead>Supplier cost</TableHead>
              <TableHead>Sell price</TableHead>
              <TableHead>Compare-at</TableHead>
              <TableHead>Stock</TableHead>
              <TableHead>
                <label className="flex items-center gap-2">
                  <input
                    ref={selectAll}
                    type="checkbox"
                    aria-label="Enable or disable all variants"
                    checked={allOn}
                    disabled={total === 0 || bulk.isPending}
                    onChange={(event) => setAll(event.target.checked)}
                    data-testid="draft-variants-select-all"
                  />
                  Status
                </label>
              </TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {product.variants.map((variant) => (
              <VariantEditorRow
                key={variant.id}
                productId={productId}
                variant={variant}
              />
            ))}
          </TableBody>
        </Table>
      </div>

      {product.variants.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No variants imported for this draft.
        </p>
      ) : null}
    </section>
  );
}
