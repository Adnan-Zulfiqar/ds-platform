"use client";

import { Truck } from "lucide-react";
import Link from "next/link";

import { FulfillmentStatusBadge } from "@/components/orders/order-status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime } from "@/lib/utils";
import { useOrders } from "@/services/orders";

/**
 * Shipment tracking list — orders that have left the warehouse.
 *
 * Detailed carrier events and the timeline live on the order detail page
 * (TrackingEvent rows written during status refresh). This page is the
 * operational inbox.
 */
export default function ShipmentsPage() {
  const { data, isLoading, isError, refetch } = useOrders({
    page: 1,
    size: 50,
    status: "shipped",
    sortBy: "external_created_at",
    sortDir: "desc",
  });

  const delivered = useOrders({
    page: 1,
    size: 25,
    status: "delivered",
    sortBy: "external_created_at",
    sortDir: "desc",
  });

  const shipped = data?.items ?? [];
  const deliveredItems = delivered.data?.items ?? [];
  const items = [...shipped, ...deliveredItems];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Shipment Tracking"
        description="In-transit and delivered orders. Open an order for carrier events and timeline."
      />

      {isError || delivered.isError ? (
        <ErrorState
          title="Could not load shipments"
          onRetry={() => {
            void refetch();
            void delivered.refetch();
          }}
        />
      ) : isLoading || delivered.isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Truck}
          title="No shipments yet"
          description="Shipments appear after order status sync records tracking numbers."
        />
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Order</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Buyer</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((order) => (
                <TableRow key={order.id}>
                  <TableCell>
                    <Link
                      href={`/orders/${order.id}`}
                      className="font-medium hover:underline"
                    >
                      {order.externalId}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <FulfillmentStatusBadge status={order.fulfillmentStatus} />
                  </TableCell>
                  <TableCell>{order.buyerName ?? "—"}</TableCell>
                  <TableCell>{formatDateTime(order.lastSyncedAt)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
