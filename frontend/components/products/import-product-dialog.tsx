"use client";

import { useState } from "react";
import { Loader2, PackagePlus } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { useImportProduct } from "@/services/products";

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
  const [formError, setFormError] = useState<string | null>(null);

  const importProduct = useImportProduct();

  function reset() {
    setExternalId("");
    setFormError(null);
    importProduct.reset();
  }

  async function handleImport() {
    setFormError(null);

    const trimmed = externalId.trim();
    if (!trimmed) {
      setFormError("Enter an AliExpress product ID.");
      return;
    }

    try {
      await importProduct.mutateAsync({ externalId: trimmed });
      setOpen(false);
      reset();
    } catch (error) {
      // The server's message is shown rather than a generic one. It
      // distinguishes "not connected" from "product not found" from "already
      // importing", and each has a different next step for the user.
      const message =
        error instanceof Error ? error.message : "The import failed.";
      setFormError(message);
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
          Import product
        </Button>
      </DialogTrigger>

      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import from AliExpress</DialogTitle>
          <DialogDescription>
            Paste the product ID from an AliExpress listing URL. Importing the
            same product again refreshes its price, stock and variants rather
            than creating a duplicate.
          </DialogDescription>
        </DialogHeader>

        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="external-id">AliExpress product ID</Label>
          <Input
            id="external-id"
            inputMode="numeric"
            placeholder="3256806389000685"
            value={externalId}
            onChange={(event) => setExternalId(event.target.value)}
            disabled={importProduct.isPending}
          />
          <p className="text-sm text-muted-foreground">
            Found in the listing URL:
            aliexpress.com/item/<strong>3256806389000685</strong>.html
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
              "Import"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
