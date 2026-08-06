"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Loader2, PackagePlus } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import {
  countryName,
  filterCountries,
  persistLastShipTo,
  readLastShipTo,
} from "@/lib/countries";
import { useImportProduct } from "@/services/products";
import { useStores, type Store } from "@/services/stores";

/** Matches the numeric id in an AliExpress listing URL. */
const ITEM_ID_IN_URL = /\/item\/(\d+)/;

type ImportFeedback = {
  title: string;
  message: string;
  action: string;
  requestId: string | null;
  listingUrl: string | null;
  draftId: string | null;
  isShipToError: boolean;
};

function storeCountry(store: Store | undefined): string | null {
  if (!store) return null;
  const settings = store.settings ?? {};
  for (const key of [
    "countryCode",
    "country_code",
    "country",
    "shipToCountry",
    "ship_to_country",
  ]) {
    const value = settings[key];
    if (typeof value === "string" && /^[A-Za-z]{2}$/.test(value)) {
      return value.toUpperCase();
    }
  }
  return null;
}

/**
 * Accept either a bare product ID or a pasted listing URL.
 *
 * The URL is what people actually have: the ID lives in the address bar, so
 * that is what gets copied. Pasting one used to send it to the server verbatim,
 * and AliExpress answers a malformed ID with *"the input parameter product_id
 * is not supplied"* — describing the value as missing rather than wrong, which
 * points at a serialisation bug that does not exist.
 *
 * The server normalises this too, and is the authority. Doing it here as well
 * means the user is told immediately rather than after a round trip.
 */
export function extractProductId(value: string): string | null {
  const candidate = value.trim();
  if (!candidate) return null;

  if (/^\d+$/.test(candidate)) return candidate;

  const inUrl = ITEM_ID_IN_URL.exec(candidate);
  if (inUrl?.[1]) return inUrl[1];

  // Recover a decorated ID only when there is exactly one candidate: choosing
  // between two would be a coin flip that imports the wrong product.
  const runs = candidate.match(/\d{6,}/g);
  return runs?.length === 1 ? (runs[0] ?? null) : null;
}

function feedbackFromError(
  error: unknown,
  productId: string | null,
): ImportFeedback {
  const listingUrl = productId
    ? `https://www.aliexpress.com/item/${productId}.html`
    : null;

  if (error instanceof ApiError) {
    const base = {
      message: error.message,
      requestId: error.requestId,
      listingUrl,
      draftId: null as string | null,
      isShipToError: error.code === "aliexpress_ship_to_prohibited",
    };

    switch (error.code) {
      case "aliexpress_ship_to_prohibited":
        return {
          ...base,
          title: "Destination not available",
          action:
            "Change the ship-to country below and try again. Your product URL is kept.",
        };
      case "aliexpress_product_unavailable":
        return {
          ...base,
          title: "Listing unavailable",
          action:
            "Open the listing on AliExpress, reconnect AliExpress if needed, or import another product.",
        };
      case "aliexpress_not_connected":
        return {
          ...base,
          title: "AliExpress not connected",
          action: "Connect AliExpress under Integrations, then retry.",
        };
      case "aliexpress_token_expired":
      case "aliexpress_auth_failed":
        return {
          ...base,
          title: "AliExpress connection expired",
          action: "Reconnect AliExpress under Integrations, then retry.",
        };
      case "aliexpress_rate_limited":
        return {
          ...base,
          title: "Rate limited",
          action: "Wait a moment, then try again.",
        };
      case "aliexpress_unavailable":
      case "aliexpress_timeout":
        return {
          ...base,
          title: "AliExpress temporarily unavailable",
          action: "Try again in a few minutes.",
        };
      case "aliexpress_invalid_response":
        return {
          ...base,
          title: "Unexpected AliExpress response",
          action: "Try again. If it keeps failing, use the reference ID below.",
        };
      case "validation_error":
        return {
          ...base,
          title: "Check import details",
          action:
            "Confirm the product ID/URL and ship-to country, then try again.",
        };
      default:
        return {
          ...base,
          title: "Import failed",
          action: "Try again. If it keeps failing, use the reference ID below.",
        };
    }
  }

  return {
    title: "Import failed",
    message: error instanceof Error ? error.message : "The import failed.",
    action: "Try again, or import another product.",
    requestId: null,
    listingUrl,
    draftId: null,
    isShipToError: false,
  };
}

/**
 * Import a product by its AliExpress identifier.
 *
 * **An identifier, not a search box.** Keyword search returns
 * `NGSELECTION_SEARCH_ERROR` on this account — verified against the live
 * gateway — so a search field would be a control that cannot work. Asking for
 * the id the seller already has is honest about what the integration supports.
 */
export function ImportProductDialog() {
  const [open, setOpen] = useState(false);
  const [externalId, setExternalId] = useState("");
  const [shipToCountry, setShipToCountry] = useState("");
  const [storeId, setStoreId] = useState("");
  const [countryQuery, setCountryQuery] = useState("");
  const [feedback, setFeedback] = useState<ImportFeedback | null>(null);
  const [successDraftId, setSuccessDraftId] = useState<string | null>(null);

  const storesQuery = useStores({ size: 50 });
  const stores = storesQuery.data?.items ?? [];
  const connectedStores = stores.filter((s) => s.status === "connected");
  const importProduct = useImportProduct();

  const recommendedCountry = useMemo(() => {
    const selected = connectedStores.find((s) => s.id === storeId);
    return (
      storeCountry(selected) ??
      storeCountry(connectedStores[0]) ??
      readLastShipTo()
    );
  }, [connectedStores, storeId]);

  useEffect(() => {
    if (!open) return;
    if (!shipToCountry) {
      setShipToCountry(recommendedCountry ?? "");
    }
  }, [open, recommendedCountry, shipToCountry]);

  useEffect(() => {
    if (!open) return;
    if (!storeId && connectedStores.length === 1) {
      setStoreId(connectedStores[0]!.id);
    }
  }, [open, connectedStores, storeId]);

  const filteredCountries = filterCountries(countryQuery);

  function resetFormFields() {
    setExternalId("");
    setShipToCountry("");
    setStoreId("");
    setCountryQuery("");
    setFeedback(null);
    setSuccessDraftId(null);
    importProduct.reset();
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      // Closing clears everything; errors mid-flow keep the URL/country.
      resetFormFields();
    }
  }

  async function handleImport() {
    setFeedback(null);
    setSuccessDraftId(null);

    const identifier = extractProductId(externalId);
    if (!identifier) {
      setFeedback({
        title: "Invalid product ID or URL",
        message: "Enter an AliExpress product ID, or paste the full listing URL.",
        action: "Use a bare ID (digits only) or a URL containing /item/<id>.",
        requestId: null,
        listingUrl: null,
        draftId: null,
        isShipToError: false,
      });
      return;
    }

    if (!shipToCountry) {
      setFeedback({
        title: "Ship-to country required",
        message:
          "Select a destination country. Availability and price depend on where the order ships.",
        action: "Choose a country below, then import again.",
        requestId: null,
        listingUrl: `https://www.aliexpress.com/item/${identifier}.html`,
        draftId: null,
        isShipToError: false,
      });
      return;
    }

    try {
      const product = await importProduct.mutateAsync({
        externalId: identifier,
        shipToCountry,
        storeId: storeId || undefined,
      });
      persistLastShipTo(shipToCountry);
      setSuccessDraftId(product.id);
      setFeedback({
        title: "Draft ready",
        message: `Imported for ${countryName(shipToCountry)}.`,
        action: "Open the draft to edit, or import another product.",
        requestId: null,
        listingUrl: null,
        draftId: product.id,
        isShipToError: false,
      });
    } catch (error) {
      // Keep externalId + shipToCountry so destination retries do not force
      // the merchant to paste the URL again.
      setFeedback(feedbackFromError(error, identifier));
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button>
          <PackagePlus className="mr-2 h-4 w-4" aria-hidden="true" />
          Import as Draft
        </Button>
      </DialogTrigger>

      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Import as Draft from AliExpress</DialogTitle>
          <DialogDescription>
            Paste an AliExpress product ID or the full listing URL. The product
            lands in Drafts for review — it does not appear under Products until
            you Publish to Store.
          </DialogDescription>
        </DialogHeader>

        {feedback ? (
          <Alert
            variant={successDraftId ? "success" : "destructive"}
            data-testid="import-feedback"
          >
            <AlertTitle>{feedback.title}</AlertTitle>
            <AlertDescription className="space-y-2">
              <p>{feedback.message}</p>
              <p className="text-sm">{feedback.action}</p>
              {feedback.requestId ? (
                <p className="font-mono text-xs opacity-80">
                  Reference: {feedback.requestId}
                </p>
              ) : null}
              {feedback.listingUrl ? (
                <p>
                  <a
                    href={feedback.listingUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="underline underline-offset-2"
                  >
                    Open listing on AliExpress
                  </a>
                </p>
              ) : null}
              {feedback.draftId ? (
                <p>
                  <Link
                    href={`/drafts/${feedback.draftId}`}
                    className="underline underline-offset-2"
                    onClick={() => handleOpenChange(false)}
                  >
                    View Draft
                  </Link>
                </p>
              ) : null}
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="external-id">AliExpress product ID or URL</Label>
          <Input
            id="external-id"
            data-testid="import-external-id"
            placeholder="1005009558589813"
            value={externalId}
            onChange={(event) => setExternalId(event.target.value)}
            disabled={importProduct.isPending}
          />
        </div>

        {connectedStores.length > 1 ? (
          <div className="space-y-2">
            <Label htmlFor="import-store">Destination store</Label>
            <select
              id="import-store"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={storeId}
              onChange={(event) => {
                const next = event.target.value;
                setStoreId(next);
                const country = storeCountry(
                  connectedStores.find((s) => s.id === next),
                );
                if (country) setShipToCountry(country);
              }}
              disabled={importProduct.isPending}
            >
              <option value="">Select a store</option>
              {connectedStores.map((store) => (
                <option key={store.id} value={store.id}>
                  {store.name}
                  {storeCountry(store)
                    ? ` — recommended: ${countryName(storeCountry(store))}`
                    : ""}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="ship-to-search">Ship-to country</Label>
          <Input
            id="ship-to-search"
            placeholder="Search countries…"
            value={countryQuery}
            onChange={(event) => setCountryQuery(event.target.value)}
            disabled={importProduct.isPending}
          />
          <select
            id="ship-to-country"
            data-testid="import-ship-to"
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            value={shipToCountry}
            onChange={(event) => setShipToCountry(event.target.value)}
            disabled={importProduct.isPending}
            required
          >
            <option value="">Select a country</option>
            {filteredCountries.map((option) => (
              <option key={option.code} value={option.code}>
                {option.name} ({option.code})
                {recommendedCountry === option.code ? " — recommended" : ""}
              </option>
            ))}
          </select>
          <p className="text-sm text-muted-foreground">
            AliExpress availability, price and shipping methods may vary by
            destination.
          </p>
        </div>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={importProduct.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={handleImport}
            disabled={importProduct.isPending || Boolean(successDraftId)}
            data-testid="import-as-draft-submit"
          >
            {importProduct.isPending ? (
              <>
                <Loader2
                  className="mr-2 h-4 w-4 animate-spin"
                  aria-hidden="true"
                />
                Importing…
              </>
            ) : feedback?.isShipToError ? (
              "Try again"
            ) : (
              "Import as Draft"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
