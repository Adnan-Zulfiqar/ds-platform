"use client";

import { useMemo, useState } from "react";
import { Monitor, Smartphone } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn, formatMoney } from "@/lib/utils";
import type { ProductDetail } from "@/types/api";

type PreviewMode = "desktop" | "mobile" | "seo";

interface DraftPreviewPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  product: ProductDetail;
  /** Live editor title so unsaved title edits still preview. */
  title: string;
  description: string;
  seoTitle: string;
  seoDescription: string;
}

function stripHtml(html: string): string {
  return html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

/**
 * DropPilot draft storefront preview — not a live Shopify storefront.
 *
 * Shows how the current draft content would read to a shopper, using local
 * product data only. Labelled “Draft Preview” so merchants are not misled.
 */
export function DraftPreviewPanel({
  open,
  onOpenChange,
  product,
  title,
  description,
  seoTitle,
  seoDescription,
}: DraftPreviewPanelProps) {
  const [mode, setMode] = useState<PreviewMode>("desktop");
  const [variantId, setVariantId] = useState<string | null>(
    product.variants[0]?.id ?? null,
  );

  const featured = product.images[0]?.url ?? null;
  const plainDescription = useMemo(
    () => stripHtml(description || product.description || ""),
    [description, product.description],
  );
  const selectedVariant =
    product.variants.find((row) => row.id === variantId) ??
    product.variants[0] ??
    null;
  const price =
    selectedVariant?.sellPrice ??
    product.sellPrice ??
    product.costPriceMin;
  const compareAt = selectedVariant?.compareAtPrice ?? null;
  const displayTitle = title.trim() || product.title || "Untitled draft";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full overflow-y-auto sm:max-w-xl lg:max-w-2xl"
        data-testid="draft-preview-panel"
      >
        <SheetHeader>
          <SheetTitle>Draft Preview</SheetTitle>
          <SheetDescription>
            How DropPilot holds this draft — not how Shopify will render it on
            your shop.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4 flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={mode === "desktop" ? "default" : "outline"}
            onClick={() => setMode("desktop")}
            aria-pressed={mode === "desktop"}
          >
            <Monitor className="mr-1.5 h-4 w-4" aria-hidden="true" />
            Desktop
          </Button>
          <Button
            size="sm"
            variant={mode === "mobile" ? "default" : "outline"}
            onClick={() => setMode("mobile")}
            aria-pressed={mode === "mobile"}
          >
            <Smartphone className="mr-1.5 h-4 w-4" aria-hidden="true" />
            Mobile
          </Button>
          <Button
            size="sm"
            variant={mode === "seo" ? "default" : "outline"}
            onClick={() => setMode("seo")}
            aria-pressed={mode === "seo"}
          >
            Search preview
          </Button>
        </div>

        {mode === "seo" ? (
          <div className="mt-6 space-y-2 rounded-lg border p-4">
            <p className="text-xs text-muted-foreground">Google-style snippet</p>
            <p className="text-lg text-[#1a0dab] dark:text-sky-400">
              {seoTitle.trim() || displayTitle}
            </p>
            <p className="text-sm text-emerald-700 dark:text-emerald-400">
              your-store.example / products /
              {(product.slug || "product-handle").replace(/^\//, "")}
            </p>
            <p className="text-sm text-muted-foreground">
              {seoDescription.trim() ||
                plainDescription.slice(0, 160) ||
                "Add an SEO description to improve this snippet."}
            </p>
          </div>
        ) : (
          <div
            className={cn(
              "mx-auto mt-6 overflow-hidden rounded-xl border bg-background shadow-sm",
              mode === "mobile" ? "max-w-[375px]" : "max-w-full",
            )}
          >
            <div className="border-b bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
              {mode === "mobile" ? "Mobile storefront frame" : "Desktop storefront frame"}
            </div>
            <div className={cn("p-4", mode === "desktop" && "sm:grid sm:grid-cols-2 sm:gap-6")}>
              <div className="overflow-hidden rounded-lg border bg-muted/20">
                {featured ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={featured}
                    alt={displayTitle}
                    className="aspect-square w-full object-cover"
                  />
                ) : (
                  <div className="flex aspect-square items-center justify-center text-sm text-muted-foreground">
                    No product image
                  </div>
                )}
              </div>

              <div className="mt-4 space-y-3 sm:mt-0">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary">Draft Preview</Badge>
                  {product.supplierName ? (
                    <Badge variant="outline">{product.supplierName}</Badge>
                  ) : null}
                </div>
                <h2 className="text-xl font-semibold tracking-tight">
                  {displayTitle}
                </h2>
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="text-2xl font-semibold tabular-nums">
                    {formatMoney(price, product.currency)}
                  </span>
                  {compareAt ? (
                    <span className="text-sm text-muted-foreground line-through tabular-nums">
                      {formatMoney(compareAt, product.currency)}
                    </span>
                  ) : null}
                </div>

                {product.variants.length > 1 ? (
                  <div className="space-y-2">
                    <p className="text-sm font-medium">Variant</p>
                    <div className="flex flex-wrap gap-2">
                      {product.variants.map((variant) => (
                        <Button
                          key={variant.id}
                          size="sm"
                          variant={
                            selectedVariant?.id === variant.id
                              ? "default"
                              : "outline"
                          }
                          onClick={() => setVariantId(variant.id)}
                        >
                          {variant.label || "Default"}
                        </Button>
                      ))}
                    </div>
                  </div>
                ) : null}

                <div className="rounded-md border bg-muted/20 p-3 text-sm">
                  <p className="font-medium">Shipping summary</p>
                  <p className="mt-1 text-muted-foreground">
                    {product.shipToCountry
                      ? `Ship-to ${product.shipToCountry}`
                      : "Ship-to not set"}
                    {product.deliveryTimeDays
                      ? ` · ~${product.deliveryTimeDays} days`
                      : ""}
                    {product.shippingCost
                      ? ` · ${formatMoney(product.shippingCost, product.currency)}`
                      : " · shipping quoted at checkout"}
                  </p>
                </div>

                <div className="space-y-2">
                  <p className="text-sm font-medium">Description</p>
                  <p className="text-sm leading-relaxed text-muted-foreground">
                    {plainDescription || "No description yet."}
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
