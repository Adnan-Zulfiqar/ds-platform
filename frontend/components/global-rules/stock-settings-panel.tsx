"use client";

import { Loader2, Package } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Callout } from "@/components/global-rules/rule-primitives";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  useStockSettings,
  useUpdateStockSettings,
  type StockSettings,
} from "@/services/global-rules";

const MAX_QUANTITY = 10000;

/**
 * Settings → Global Rules → Stock.
 *
 * One number for every sales channel, instead of AliExpress's warehouse
 * total. The rule itself lives on the server (`listed_quantity`); this
 * panel only says it in words, so the two cannot disagree about what a
 * sold-out product does.
 */
export function StockSettingsPanel({ canManage }: { canManage: boolean }) {
  const settings = useStockSettings();
  // Here, not in the form: a save changes the form's key and remounts it.
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  if (settings.isPending) return <Skeleton className="h-48 w-full" />;
  if (settings.isError) {
    return (
      <ErrorState
        title="Couldn't load the stock setting"
        description="Try again in a moment."
        onRetry={() => void settings.refetch()}
      />
    );
  }
  // Keyed on the saved value so the form starts from it, and starts again
  // from a value saved elsewhere, without copying server state in an effect.
  return (
    <StockSettingsForm
      key={String(settings.data.publishQuantity)}
      saved={settings.data}
      canManage={canManage}
      savedMessage={savedMessage}
      onSaved={setSavedMessage}
    />
  );
}

function StockSettingsForm({
  saved,
  canManage,
  savedMessage,
  onSaved,
}: {
  saved: StockSettings;
  canManage: boolean;
  savedMessage: string | null;
  onSaved: (message: string | null) => void;
}) {
  const update = useUpdateStockSettings();
  const [useFixed, setUseFixed] = useState(saved.publishQuantity !== null);
  const [quantity, setQuantity] = useState(String(saved.publishQuantity ?? 10));
  const [error, setError] = useState<string | null>(null);

  const parsed = Number(quantity);
  const quantityValid =
    /^\d+$/.test(quantity) && parsed >= 1 && parsed <= MAX_QUANTITY;
  const next: number | null = useFixed ? parsed : null;
  const unchanged = useFixed
    ? quantityValid && parsed === saved.publishQuantity
    : saved.publishQuantity === null;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    onSaved(null);
    if (useFixed && !quantityValid) {
      setError(`Enter a whole number from 1 to ${MAX_QUANTITY.toLocaleString()}.`);
      return;
    }
    try {
      const result = await update.mutateAsync({ publishQuantity: next });
      onSaved(
        result.publishQuantity === null
          ? "Saved. Channels will list the supplier's stock."
          : `Saved. Channels will list ${result.publishQuantity} of each product.`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save. Try again.");
    }
  }

  return (
    <form
      onSubmit={(event) => void onSubmit(event)}
      className="space-y-4 rounded-lg border bg-card p-5"
      data-testid="stock-settings"
    >
      <div className="flex items-start gap-3">
        <Package className="mt-0.5 h-5 w-5 text-muted-foreground" aria-hidden="true" />
        <div>
          <h2 className="text-base font-semibold">Listed stock</h2>
          <p className="text-sm text-muted-foreground">
            The quantity your Shopify, eBay and WooCommerce listings show.
          </p>
        </div>
      </div>

      <fieldset className="space-y-3" disabled={!canManage || update.isPending}>
        <legend className="sr-only">How much stock to list</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name="stock-mode"
            className="mt-1"
            checked={!useFixed}
            onChange={() => setUseFixed(false)}
          />
          <span>
            <span className="font-medium">Supplier&apos;s stock</span>
            <span className="block text-muted-foreground">
              List whatever AliExpress reports for each product.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name="stock-mode"
            className="mt-1"
            checked={useFixed}
            onChange={() => setUseFixed(true)}
          />
          <span>
            <span className="font-medium">The same quantity for every product</span>
            <span className="block text-muted-foreground">
              For example 14, whatever the supplier&apos;s warehouse holds.
            </span>
          </span>
        </label>
        {useFixed ? (
          <div className="max-w-[12rem] space-y-1.5 pl-6">
            <Label htmlFor="stock-publish-quantity">Quantity to list</Label>
            <Input
              id="stock-publish-quantity"
              inputMode="numeric"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value.trim())}
              aria-invalid={!quantityValid}
              aria-describedby="stock-publish-quantity-hint"
            />
          </div>
        ) : null}
      </fieldset>

      <Callout title="When the supplier sells out">
        <p id="stock-publish-quantity-hint">
          A product whose supplier has none left is listed at 0, so it stops
          selling instead of taking orders you can&apos;t fulfil. Listings pick up
          a change at their next publish or stock update.
        </p>
      </Callout>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {savedMessage ? (
        <p role="status" className="text-sm text-success">
          {savedMessage}
        </p>
      ) : null}

      {canManage ? (
        <Button type="submit" disabled={update.isPending || unchanged}>
          {update.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          Save
        </Button>
      ) : null}
    </form>
  );
}
