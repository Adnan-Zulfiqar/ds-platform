import { Badge } from "@/components/ui/badge";
import type { FulfillmentStatus, PaymentStatus, ShipmentStatus } from "@/types/api";

/**
 * Status badges for the order domain.
 *
 * One module owns the status-to-variant mapping so the list, the detail page,
 * and the dashboard cannot drift into colouring the same status differently.
 * Labels replace underscores because `awaiting_payment` is a database value,
 * not a sentence.
 */

const FULFILLMENT_VARIANT: Record<
  FulfillmentStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  pending: "secondary",
  awaiting_payment: "secondary",
  paid: "outline",
  processing: "outline",
  fulfilled: "outline",
  shipped: "default",
  delivered: "default",
  cancelled: "destructive",
  refunded: "destructive",
  disputed: "destructive",
};

const PAYMENT_VARIANT: Record<
  PaymentStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  unknown: "secondary",
  unpaid: "outline",
  paid: "default",
  refunded: "destructive",
};

const SHIPMENT_VARIANT: Record<
  ShipmentStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  pending: "secondary",
  in_transit: "outline",
  out_for_delivery: "outline",
  delivered: "default",
  exception: "destructive",
  returned: "destructive",
};

export function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

export function FulfillmentStatusBadge({ status }: { status: FulfillmentStatus }) {
  return (
    <Badge variant={FULFILLMENT_VARIANT[status]} className="capitalize">
      {statusLabel(status)}
    </Badge>
  );
}

export function PaymentStatusBadge({ status }: { status: PaymentStatus }) {
  return (
    <Badge variant={PAYMENT_VARIANT[status]} className="capitalize">
      {statusLabel(status)}
    </Badge>
  );
}

export function ShipmentStatusBadge({ status }: { status: ShipmentStatus }) {
  return (
    <Badge variant={SHIPMENT_VARIANT[status]} className="capitalize">
      {statusLabel(status)}
    </Badge>
  );
}
