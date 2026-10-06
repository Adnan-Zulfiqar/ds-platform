"use client";

import { ExternalLink } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import {
  type SupplierOrderStatus,
  usePlaceSupplierOrder,
  usePushSupplierTracking,
  useSupplierOrder,
} from "@/services/orders";
import type { OrderDetail } from "@/types/api";

const CHANNELS = new Set(["shopify", "ebay", "woocommerce"]);

const STATUS: Record<SupplierOrderStatus, { label: string; tone: "secondary" | "warning" | "success" | "destructive" }> = {
  none: { label: "Not sent to AliExpress", tone: "secondary" },
  needs_review: { label: "Needs review", tone: "warning" },
  queued: { label: "Sending to AliExpress…", tone: "secondary" },
  placing: { label: "Sending to AliExpress…", tone: "secondary" },
  placed: { label: "Placed, awaiting payment on AliExpress", tone: "warning" },
  failed: { label: "AliExpress refused the order", tone: "destructive" },
  shipped: { label: "Tracking sent to the store", tone: "success" },
};

/** Reason codes from the server's review, in words a merchant can act on.
 * Line-specific codes arrive as `line_<id>:<reason>`. */
function reasonText(code: string): string {
  const [line, reason] = code.includes(":") ? code.split(":", 2) : [null, code];
  const where = line ? `Line ${line.replace(/^line_/, "")}: ` : "";
  const text: Record<string, string> = {
    order_not_paid: "The order is not paid yet.",
    order_cancelled: "The order was cancelled.",
    not_a_channel_order: "Only Shopify, eBay and WooCommerce orders can be placed.",
    missing_recipient_name: "The shipping address has no name.",
    missing_recipient_phone: "The shipping address has no phone number (AliExpress requires one).",
    missing_address_line1: "The shipping address has no street.",
    missing_city: "The shipping address has no city.",
    missing_country_code: "The shipping address has no country.",
    tax_id_required: "AliExpress needs the buyer's tax id for this country; place it on AliExpress by hand.",
    no_order_lines: "The order has no lines.",
    not_a_droppilot_product: "this product was not published from DropPilot.",
    not_an_aliexpress_product: "this product is not from AliExpress.",
    variant_unknown: "the store did not say which variant was sold.",
    no_supplier_sku: "this variant has no AliExpress SKU.",
  };
  return where + (text[reason] ?? reason);
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

/**
 * Track F: the AliExpress order behind this customer order. Shown for
 * Shopify, eBay and WooCommerce orders. Admins and owners act; others see
 * the state. Payment happens on AliExpress, so a placed order links there.
 */
export function SupplierOrderPanel({ order }: { order: OrderDetail }) {
  const { hasRole } = useAuth();
  const canAct = hasRole("owner") || hasRole("admin");
  const supplier = useSupplierOrder(order.id);
  const place = usePlaceSupplierOrder(order.id);
  const push = usePushSupplierTracking(order.id);

  if (!CHANNELS.has(order.source)) return null;
  if (supplier.isLoading || !supplier.data) return <Skeleton className="h-32 w-full" />;

  const row = supplier.data;
  const state = STATUS[row.status];
  const canPlace = canAct && ["none", "needs_review", "failed"].includes(row.status);
  const canPush = canAct && row.status === "placed" && !!row.trackingNumber && row.externalOrderIds.length === 1;
  const failure = place.error ?? push.error;

  return (
    <Card data-testid="supplier-order-panel">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">Supplier order</CardTitle>
          <Badge variant={state.tone} data-testid="supplier-order-status">
            {state.label}
          </Badge>
        </div>
        <CardDescription>
          AliExpress creates the order unpaid. You pay it on AliExpress; DropPilot never pays.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {row.reviewReasons.length > 0 && (
          <ul className="list-disc space-y-1 pl-5" data-testid="supplier-order-reasons">
            {row.reviewReasons.map((code) => (
              <li key={code}>{reasonText(code)}</li>
            ))}
          </ul>
        )}
        {row.status === "failed" && row.errorMessage && (
          <p className="text-destructive">{row.errorMessage}</p>
        )}
        {row.status === "placing" && (
          <p className="text-muted-foreground">
            If this does not finish within a few minutes, check your AliExpress orders before
            trying again, so the goods are not bought twice.
          </p>
        )}
        {row.externalOrderIds.length > 0 && (
          <p>
            AliExpress order{row.externalOrderIds.length > 1 ? "s" : ""}:{" "}
            <span className="font-mono">{row.externalOrderIds.join(", ")}</span>
            {row.placedAt && <> · placed {formatDateTime(row.placedAt)}</>}
          </p>
        )}
        {row.trackingNumber && (
          <p>
            Tracking: <span className="font-mono">{row.trackingNumber}</span>
            {row.trackingCarrier && <> ({row.trackingCarrier})</>}
            {row.trackingPushedAt && <> · sent to the store {formatDateTime(row.trackingPushedAt)}</>}
          </p>
        )}
        {row.errorCode === "multiple_parcels" && (
          <p className="text-muted-foreground">{row.errorMessage}</p>
        )}
        {failure && (
          <Alert variant="destructive">
            <AlertDescription>{errorText(failure)}</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap gap-2">
          {canPlace && (
            <Button disabled={place.isPending} onClick={() => place.mutate()}>
              {row.status === "none" ? "Place on AliExpress" : "Try again"}
            </Button>
          )}
          {row.paymentUrl && (
            <Button asChild variant="outline">
              <a href={row.paymentUrl} target="_blank" rel="noreferrer">
                Pay on AliExpress <ExternalLink className="ml-1 h-3 w-3" aria-hidden="true" />
              </a>
            </Button>
          )}
          {canPush && (
            <Button variant="outline" disabled={push.isPending} onClick={() => push.mutate()}>
              Send tracking to the store
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
