"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Download, Loader2, Truck } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import { useAuth } from "@/providers/auth-provider";
import { importEbayOrders, shipEbayOrder, useEbayStatus } from "@/services/integrations";
import { orderKeys } from "@/services/orders";
import type { OrderDetail } from "@/types/api";

/**
 * EBAY-C5: eBay orders on the Orders pages.
 *
 * Import is on demand (the last seven days, re-importing updates). Shipping
 * sends carrier and tracking to eBay for every line item; repeating the same
 * tracking number changes nothing.
 */

/** eBay carrier codes for the carriers merchants use most; anything else via "Other". */
const CARRIERS: { code: string; label: string }[] = [
  { code: "USPS", label: "USPS" },
  { code: "UPS", label: "UPS" },
  { code: "FEDEX", label: "FedEx" },
  { code: "DHL", label: "DHL" },
  { code: "ROYAL_MAIL", label: "Royal Mail" },
  { code: "CHINA_POST", label: "China Post" },
  { code: "YANWEN", label: "Yanwen" },
  { code: "4PX", label: "4PX" },
];

function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function EbayImportOrdersButton() {
  const { hasRole } = useAuth();
  const status = useEbayStatus();
  const queryClient = useQueryClient();
  const importOrders = useMutation({
    mutationFn: () => importEbayOrders(7),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: orderKeys.all });
    },
  });
  if (!status.data?.connected || !(hasRole("owner") || hasRole("admin"))) return null;

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="outline"
        onClick={() => importOrders.mutate()}
        disabled={importOrders.isPending}
        data-testid="ebay-import-orders"
      >
        {importOrders.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        ) : (
          <Download className="mr-2 h-4 w-4" aria-hidden="true" />
        )}
        Import eBay orders
      </Button>
      {importOrders.isSuccess ? (
        <p className="text-xs text-muted-foreground" role="status" data-testid="ebay-import-result">
          {importOrders.data.created} new, {importOrders.data.updated} updated from eBay.
        </p>
      ) : null}
      {importOrders.isError ? (
        <p className="text-xs text-destructive" role="alert">
          {message(importOrders.error, "Could not import eBay orders.")}
        </p>
      ) : null}
    </div>
  );
}

export function EbayShipOrderForm({ order }: { order: OrderDetail }) {
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const [carrier, setCarrier] = useState("USPS");
  const [otherCarrier, setOtherCarrier] = useState("");
  const [tracking, setTracking] = useState("");
  const ship = useMutation({
    mutationFn: () =>
      shipEbayOrder(order.id, {
        carrierCode: carrier === "OTHER" ? otherCarrier.trim() : carrier,
        trackingNumber: tracking.trim(),
      }),
    onSuccess: () => {
      setTracking("");
      void queryClient.invalidateQueries({ queryKey: orderKeys.all });
    },
  });

  if (order.source !== "ebay" || !(hasRole("owner") || hasRole("admin"))) return null;
  if (order.fulfillmentStatus === "cancelled") {
    return <p className="text-sm text-muted-foreground">This order was cancelled on eBay.</p>;
  }
  const carrierCode = carrier === "OTHER" ? otherCarrier.trim() : carrier;
  const valid = /^[A-Za-z0-9_-]{2,64}$/.test(carrierCode) && /^[A-Za-z0-9-]{4,64}$/.test(tracking.trim());

  return (
    <div className="space-y-3 rounded-md border p-3" data-testid="ebay-ship-form">
      <h3 className="text-sm font-semibold">Mark shipped on eBay</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="ebay-carrier">Carrier</Label>
          <select
            id="ebay-carrier"
            className="h-10 w-full rounded-md border bg-background px-3 text-sm"
            value={carrier}
            onChange={(event) => setCarrier(event.target.value)}
          >
            {CARRIERS.map((c) => (
              <option key={c.code} value={c.code}>
                {c.label}
              </option>
            ))}
            <option value="OTHER">Other (eBay carrier code)</option>
          </select>
        </div>
        {carrier === "OTHER" ? (
          <div className="space-y-1">
            <Label htmlFor="ebay-carrier-other">eBay carrier code</Label>
            <Input id="ebay-carrier-other" value={otherCarrier} onChange={(event) => setOtherCarrier(event.target.value)} />
          </div>
        ) : null}
        <div className="space-y-1">
          <Label htmlFor="ebay-tracking">Tracking number</Label>
          <Input id="ebay-tracking" value={tracking} onChange={(event) => setTracking(event.target.value)} />
        </div>
      </div>
      {ship.isError ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{message(ship.error, "eBay did not accept the shipment.")}</AlertDescription>
        </Alert>
      ) : null}
      {ship.isSuccess ? (
        <p className="text-sm text-muted-foreground" role="status" data-testid="ebay-ship-result">
          eBay has the tracking number.
        </p>
      ) : null}
      <Button className="min-h-11 sm:min-h-9" onClick={() => ship.mutate()} disabled={!valid || ship.isPending}>
        {ship.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        ) : (
          <Truck className="mr-2 h-4 w-4" aria-hidden="true" />
        )}
        Send to eBay
      </Button>
    </div>
  );
}
