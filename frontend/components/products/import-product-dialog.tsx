"use client";

import { useState } from "react";
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
import { useImportProduct } from "@/services/products";

/** Matches the numeric id in an AliExpress listing URL. */
const ITEM_ID_IN_URL = /\/item\/(\d+)/;

const SHIP_TO_OPTIONS = [
  { value: "US", label: "United States (US)" },
  { value: "GB", label: "United Kingdom (GB)" },
  { value: "CA", label: "Canada (CA)" },
  { value: "AU", label: "Australia (AU)" },
  { value: "DE", label: "Germany (DE)" },
  { value: "FR", label: "France (FR)" },
  { value: "NL", label: "Netherlands (NL)" },
  { value: "IT", label: "Italy (IT)" },
  { value: "ES", label: "Spain (ES)" },
  { value: "PL", label: "Poland (PL)" },
] as const;

type ImportFeedback = {
  title: string;
  message: string;
  action: string;
  requestId: string | null;
  listingUrl: string | null;
};

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
    };

    switch (error.code) {
      case "aliexpress_ship_to_prohibited":
        return {
          ...base,
          title: "Ship-to country not allowed",
          action:
            "Change Ship to country below and try again, or open the listing on AliExpress to see where it ships.",
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
      case "aliexpress_reauth_required":
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
      case "validation_error":
        return {
          ...base,
          title: "Invalid product ID or URL",
          action: "Paste a bare AliExpress product ID or the full listing URL.",
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
  const [shipToCountry, setShipToCountry] = useState("US");
  const [feedback, setFeedback] = useState<ImportFeedback | null>(null);

  const importProduct = useImportProduct();

  function reset() {
    setExternalId("");
    setShipToCountry("US");
    setFeedback(null);
    importProduct.reset();
  }

  async function handleImport() {
    setFeedback(null);

    const identifier = extractProductId(externalId);
    if (!identifier) {
      setFeedback({
        title: "Invalid product ID or URL",
        message: "Enter an AliExpress product ID, or paste the full listing URL.",
        action: "Use a bare ID (digits only) or a URL containing /item/<id>.",
        requestId: null,
        listingUrl: null,
      });
      return;
    }

    try {
      await importProduct.mutateAsync({
        externalId: identifier,
        shipToCountry,
      });
      setOpen(false);
      reset();
    } catch (error) {
      setFeedback(feedbackFromError(error, identifier));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <PackagePlus className="mr-2 h-4 w-4" aria-hidden="true" />
          Import as Draft
        </Button>
      </DialogTrigger>

      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import as Draft from AliExpress</DialogTitle>
          <DialogDescription>
            Paste an AliExpress product ID or the full listing URL. The product
            lands in Drafts for review — it does not appear under Products until
            you Publish to Store. Importing the same product again refreshes
            supplier data rather than creating a duplicate.
          </DialogDescription>
        </DialogHeader>

        {feedback ? (
          <Alert variant="destructive">
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
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="external-id">AliExpress product ID or URL</Label>
          <Input
            id="external-id"
            placeholder="1005009558589813"
            value={externalId}
            onChange={(event) => setExternalId(event.target.value)}
            disabled={importProduct.isPending}
          />
          <p className="text-sm text-muted-foreground">
            Paste either the product ID or the whole listing URL — both work.
          </p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="ship-to-country">Ship to country</Label>
          <select
            id="ship-to-country"
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            value={shipToCountry}
            onChange={(event) => setShipToCountry(event.target.value)}
            disabled={importProduct.isPending}
          >
            {SHIP_TO_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <p className="text-sm text-muted-foreground">
            Some listings are blocked for certain destinations. If import fails
            with a ship-to error, try another country.
          </p>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={importProduct.isPending}
          >
            Cancel
          </Button>
          <Button onClick={handleImport} disabled={importProduct.isPending}>
            {importProduct.isPending ? (
              <>
                <Loader2
                  className="mr-2 h-4 w-4 animate-spin"
                  aria-hidden="true"
                />
                Importing…
              </>
            ) : (
              "Import as Draft"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
