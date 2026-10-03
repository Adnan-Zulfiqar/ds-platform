"use client";

import { useMutation } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2, Search } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { useAuth } from "@/providers/auth-provider";
import {
  fetchEbayCategorySuggestions,
  useEbayStatus,
  useEbayProductDetails,
  useSaveEbayProductDetails,
} from "@/services/integrations";
import type { EbayAspect, EbayCategorySuggestion, EbayProductDetails } from "@/types/api";

/**
 * EBAY-C3: the eBay category and item specifics for this product.
 *
 * eBay will not accept a listing without a leaf category and the aspects that
 * category requires. Suggestions come from eBay itself, from the title unless
 * the merchant types a better description. Saving stores the choice; publish
 * readiness re-checks it against eBay's current requirements.
 */

const SELECT_CLASS = "h-10 w-full rounded-md border bg-background px-3 text-sm disabled:opacity-60";

const MARKETPLACES: Record<string, string> = {
  EBAY_US: "United States",
  EBAY_GB: "United Kingdom",
  EBAY_DE: "Germany",
  EBAY_AU: "Australia",
  EBAY_CA: "Canada",
  EBAY_FR: "France",
  EBAY_IT: "Italy",
  EBAY_ES: "Spain",
};

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function EbayProductDetailsSection({
  productId,
  initialMarketplace,
  canEdit,
}: {
  productId: string;
  initialMarketplace: string | null;
  canEdit: boolean;
}) {
  const [marketplaceId, setMarketplaceId] = useState(
    initialMarketplace && initialMarketplace in MARKETPLACES ? initialMarketplace : "EBAY_US",
  );
  const details = useEbayProductDetails(productId, marketplaceId, true);

  return (
    <section
      aria-labelledby="ebay-details-title"
      className="space-y-4 rounded-md border p-4"
      data-testid="ebay-product-details"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 id="ebay-details-title" className="text-sm font-semibold">
            eBay details
          </h3>
          <p className="text-sm text-muted-foreground">
            The eBay category and item specifics this product will be listed with.
          </p>
        </div>
        <div className="space-y-1 sm:w-56">
          <Label htmlFor="ebay-details-marketplace">Marketplace</Label>
          <select
            id="ebay-details-marketplace"
            className={SELECT_CLASS}
            value={marketplaceId}
            onChange={(event) => setMarketplaceId(event.target.value)}
          >
            {Object.entries(MARKETPLACES).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {details.isPending ? (
        <div className="space-y-2" aria-busy="true">
          <Skeleton className="h-10 w-full" />
        </div>
      ) : details.isError ? (
        <Alert variant="destructive" data-testid="ebay-details-error">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            {errorMessage(details.error, "Could not load the eBay details. Please try again.")}
          </AlertDescription>
        </Alert>
      ) : (
        <DetailsForm
          key={`${marketplaceId}:${details.data.categoryId ?? "none"}`}
          productId={productId}
          details={details.data}
          canEdit={canEdit}
        />
      )}
    </section>
  );
}

function DetailsForm({
  productId,
  details,
  canEdit,
}: {
  productId: string;
  details: EbayProductDetails;
  canEdit: boolean;
}) {
  const save = useSaveEbayProductDetails(productId);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<EbayCategorySuggestion | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const suggest = useMutation({
    mutationFn: () => fetchEbayCategorySuggestions(productId, details.marketplaceId, query.trim() || undefined),
  });

  const categoryId = picked?.categoryId ?? details.categoryId;
  const categoryLabel = picked?.path ?? details.categoryName;
  // A different category than the saved one has unknown aspects until saved.
  const aspectsKnown = picked === null || picked.categoryId === details.categoryId;
  const valueOf = (aspect: EbayAspect): string =>
    edits[aspect.name] ?? (details.aspects[aspect.name] ?? []).join(", ");

  async function handleSave() {
    if (!categoryId) return;
    setSaveError(null);
    setSaved(false);
    const aspects: Record<string, string[]> = {};
    if (aspectsKnown) {
      for (const aspect of details.categoryAspects) {
        const raw = valueOf(aspect);
        const values = aspect.multiple ? raw.split(",") : [raw];
        const clean = values.map((v) => v.trim()).filter(Boolean);
        if (clean.length > 0) aspects[aspect.name] = clean;
      }
    }
    try {
      await save.mutateAsync({
        marketplaceId: details.marketplaceId,
        categoryId,
        categoryName: categoryLabel,
        aspects,
      });
      setSaved(true);
    } catch (error) {
      setSaveError(errorMessage(error, "Could not save the eBay details."));
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="ebay-category-query">Category</Label>
        <p className="text-sm" data-testid="ebay-current-category">
          {categoryLabel ?? <span className="text-muted-foreground">No category chosen yet.</span>}
        </p>
        {canEdit ? (
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              id="ebay-category-query"
              placeholder="Describe the product, or leave empty to use the title"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Button
              variant="outline"
              className="min-h-11 sm:min-h-9"
              onClick={() => suggest.mutate()}
              disabled={suggest.isPending}
            >
              {suggest.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <Search className="mr-2 h-4 w-4" aria-hidden="true" />
              )}
              Suggest categories
            </Button>
          </div>
        ) : null}
        {suggest.isError ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{errorMessage(suggest.error, "eBay did not suggest a category.")}</AlertDescription>
          </Alert>
        ) : null}
        {suggest.data ? (
          suggest.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No suggestions — try different words.</p>
          ) : (
            <ul className="space-y-1" data-testid="ebay-category-suggestions">
              {suggest.data.map((suggestion) => (
                <li key={suggestion.categoryId}>
                  <Button
                    variant={picked?.categoryId === suggestion.categoryId ? "secondary" : "ghost"}
                    className="h-auto min-h-9 w-full justify-start whitespace-normal text-left"
                    onClick={() => {
                      setSaved(false);
                      setPicked(suggestion);
                    }}
                  >
                    {suggestion.path}
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : null}
      </div>

      {categoryId && aspectsKnown && details.categoryAspects.length > 0 ? (
        <div className="space-y-3">
          <h4 className="text-sm font-medium">Item specifics</h4>
          <div className="grid gap-3 sm:grid-cols-2">
            {details.categoryAspects.map((aspect) => (
              <AspectField
                key={aspect.name}
                aspect={aspect}
                value={valueOf(aspect)}
                disabled={!canEdit}
                onChange={(next) => {
                  setSaved(false);
                  setEdits((current) => ({ ...current, [aspect.name]: next }));
                }}
              />
            ))}
          </div>
        </div>
      ) : null}

      {categoryId && !aspectsKnown ? (
        <p className="text-sm text-muted-foreground">
          Save to see the item specifics eBay asks for in this category.
        </p>
      ) : null}

      {details.missingRequired.length > 0 && aspectsKnown ? (
        <Alert data-testid="ebay-missing-required">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            eBay requires: {details.missingRequired.join(", ")}.
          </AlertDescription>
        </Alert>
      ) : null}

      {saveError ? (
        <Alert variant="destructive" data-testid="ebay-details-save-error">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{saveError}</AlertDescription>
        </Alert>
      ) : null}

      {canEdit ? (
        <div className="flex items-center gap-3">
          <Button className="min-h-11 sm:min-h-9" onClick={() => void handleSave()} disabled={!categoryId || save.isPending}>
            {save.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : null}
            Save eBay details
          </Button>
          {saved ? (
            <span className="flex items-center text-sm text-muted-foreground" role="status" data-testid="ebay-details-saved">
              <CheckCircle2 className="mr-1 h-4 w-4" aria-hidden="true" />
              Saved
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function AspectField({
  aspect,
  value,
  disabled,
  onChange,
}: {
  aspect: EbayAspect;
  value: string;
  disabled: boolean;
  onChange: (next: string) => void;
}) {
  const id = `ebay-aspect-${aspect.name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
  const label = `${aspect.name}${aspect.required ? " (required)" : ""}`;
  if (aspect.selectionOnly && !aspect.multiple && aspect.values.length > 0) {
    return (
      <div className="space-y-1">
        <Label htmlFor={id}>{label}</Label>
        <select
          id={id}
          className={SELECT_CLASS}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Choose…</option>
          {aspect.values.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>
    );
  }
  const listId = aspect.values.length > 0 ? `${id}-values` : undefined;
  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        list={listId}
        value={value}
        disabled={disabled}
        placeholder={aspect.multiple ? "Separate values with commas" : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {listId ? (
        <datalist id={listId}>
          {aspect.values.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
      ) : null}
    </div>
  );
}

/**
 * Shown only when this workspace has a working eBay connection and the viewer
 * may read seller setup; anything else (no connection, status unavailable)
 * renders nothing rather than an eBay error inside a Shopify workflow.
 */
export function EbayProductDetailsIfConnected({ productId }: { productId: string }) {
  const { hasRole } = useAuth();
  const status = useEbayStatus();
  const canEdit = hasRole("owner") || hasRole("admin");
  if (!status.data?.connected || !(canEdit || hasRole("member"))) return null;
  return (
    <EbayProductDetailsSection
      productId={productId}
      initialMarketplace={status.data.connection?.marketplaceId ?? null}
      canEdit={canEdit}
    />
  );
}
