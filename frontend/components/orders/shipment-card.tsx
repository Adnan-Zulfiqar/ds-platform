import { Truck } from "lucide-react";

import { ShipmentStatusBadge } from "@/components/orders/order-status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/utils";
import type { Shipment } from "@/types/api";

/**
 * One shipment with its tracking history.
 *
 * Pure presentation — the parent already holds the detail response, so this
 * component fetches nothing and can stay a Server-Component-compatible leaf.
 */
export function ShipmentCard({ shipment }: { shipment: Shipment }) {
  return (
    <Card data-testid="shipment-card">
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <Truck className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            {shipment.trackingNumber ?? "Tracking pending"}
          </CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            {[shipment.carrier, shipment.serviceName].filter(Boolean).join(" · ") ||
              "Carrier not yet known"}
          </p>
        </div>
        <ShipmentStatusBadge status={shipment.status} />
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-foreground">Shipped</dt>
            <dd>{formatDateTime(shipment.shippedAt)}</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-foreground">Estimated delivery</dt>
            <dd>{formatDateTime(shipment.estimatedDeliveryAt)}</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-foreground">Current location</dt>
            <dd>{shipment.currentLocation ?? "—"}</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-foreground">Last checked</dt>
            <dd>{formatDateTime(shipment.lastCheckedAt)}</dd>
          </div>
        </dl>

        {shipment.trackingEvents.length > 0 && (
          <div>
            <h4 className="mb-2 text-sm font-medium">Tracking history</h4>
            <ol className="space-y-2">
              {shipment.trackingEvents.map((event) => (
                <li key={event.id} className="text-sm">
                  <p className="font-medium">
                    {event.status ?? event.description ?? "Update"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatDateTime(event.occurredAt)}
                    {event.location ? ` · ${event.location}` : ""}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
