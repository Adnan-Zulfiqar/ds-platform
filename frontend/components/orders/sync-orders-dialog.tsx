"use client";

import { Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

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
import { useSyncOrders } from "@/services/orders";
import type { OrderSyncRun } from "@/types/api";

/**
 * Trigger one incremental order synchronisation.
 *
 * The result stays in the dialog rather than closing it: a sync that found
 * nothing and a sync that imported forty orders both "succeed", and the
 * numbers are the only way the user can tell which happened.
 */
export function SyncOrdersDialog() {
  const [open, setOpen] = useState(false);
  const [sinceDays, setSinceDays] = useState("7");
  const [formError, setFormError] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<OrderSyncRun | null>(null);

  const syncOrders = useSyncOrders();

  function reset() {
    setSinceDays("7");
    setFormError(null);
    setLastRun(null);
    syncOrders.reset();
  }

  async function handleSync() {
    setFormError(null);
    setLastRun(null);

    const days = Number(sinceDays);
    if (!Number.isInteger(days) || days < 1 || days > 90) {
      setFormError("Enter a window between 1 and 90 days.");
      return;
    }

    try {
      const run = await syncOrders.mutateAsync({ sinceDays: days });
      setLastRun(run);
    } catch (error) {
      // The server's message distinguishes "not connected" from "a sync is
      // already running" from a supplier-side failure; each has a different
      // next step for the user.
      const message = error instanceof Error ? error.message : "The sync failed.";
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
        <Button data-testid="sync-orders-button">
          <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
          Sync orders
        </Button>
      </DialogTrigger>

      <DialogContent>
        <DialogHeader>
          <DialogTitle>Synchronise orders</DialogTitle>
          <DialogDescription>
            Imports orders placed on AliExpress within the chosen window.
            Running it again updates existing orders rather than duplicating
            them.
          </DialogDescription>
        </DialogHeader>

        {formError ? (
          <Alert variant="destructive" data-testid="sync-error">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        {lastRun ? (
          <Alert data-testid="sync-result">
            <AlertTitle>Sync {lastRun.status}</AlertTitle>
            <AlertDescription>
              {lastRun.ordersSeen} seen, {lastRun.ordersCreated} created,{" "}
              {lastRun.ordersUpdated} updated.
            </AlertDescription>
          </Alert>
        ) : null}

        <div className="space-y-2">
          <Label htmlFor="since-days">Window (days)</Label>
          <Input
            id="since-days"
            inputMode="numeric"
            value={sinceDays}
            onChange={(event) => setSinceDays(event.target.value)}
            disabled={syncOrders.isPending}
          />
          <p className="text-sm text-muted-foreground">
            How far back to ask AliExpress for orders. Longer windows use more
            of your API quota.
          </p>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={syncOrders.isPending}
          >
            Close
          </Button>
          <Button onClick={handleSync} disabled={syncOrders.isPending}>
            {syncOrders.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                Syncing…
              </>
            ) : (
              "Sync now"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
