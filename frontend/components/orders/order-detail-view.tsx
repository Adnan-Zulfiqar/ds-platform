"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import {
  FulfillmentStatusBadge,
  PaymentStatusBadge,
} from "@/components/orders/order-status-badge";
import {
  EbayShipOrderForm,
  ShopifyShipOrderForm,
  WooCommerceShipOrderForm,
} from "@/components/orders/ebay-orders";
import { OrderTimeline } from "@/components/orders/order-timeline";
import { ShipmentCard } from "@/components/orders/shipment-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { formatDateTime, formatMoney } from "@/lib/utils";
import { useOrder } from "@/services/orders";
import type { OrderDetail } from "@/types/api";

function formatAddress(order: OrderDetail): string[] {
  const cityLine = [order.city, order.province, order.postalCode]
    .filter(Boolean)
    .join(", ");
  return [
    order.recipientName,
    order.addressLine1,
    order.addressLine2,
    cityLine,
    order.countryCode ?? order.buyerCountry,
  ].filter((line): line is string => Boolean(line));
}

export function OrderDetailView({ orderId }: { orderId: string }) {
  const { data: order, isPending, isError, error, refetch } = useOrder(orderId);

  if (isPending) {
    return (
      <div className="space-y-4" data-testid="order-detail-loading">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Could not load the order"
        description={error instanceof Error ? error.message : "Please try again."}
        onRetry={() => void refetch()}
      />
    );
  }

  const address = formatAddress(order);

  return (
    <div className="space-y-6" data-testid="order-detail">
      <div>
        <Button variant="ghost" size="sm" asChild className="-ml-2 mb-2">
          <Link href="/orders">
            <ArrowLeft className="mr-1 h-4 w-4" aria-hidden="true" />
            Back to orders
          </Link>
        </Button>
        <PageHeader
          title={`Order ${order.externalId}`}
          description={`Placed ${formatDateTime(order.externalCreatedAt)} · last synced ${formatDateTime(order.lastSyncedAt)}`}
          actions={
            <div className="flex items-center gap-2">
              <FulfillmentStatusBadge status={order.fulfillmentStatus} />
              <PaymentStatusBadge status={order.paymentStatus} />
            </div>
          }
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Total</dt>
                <dd className="font-medium tabular-nums">
                  {formatMoney(order.totalAmount, order.currency)}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Shipping</dt>
                <dd className="tabular-nums">
                  {formatMoney(order.shippingAmount, order.currency)}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Paid</dt>
                <dd>{formatDateTime(order.paidAt)}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Delivered</dt>
                <dd>{formatDateTime(order.deliveredAt)}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Supplier status</dt>
                <dd>{order.externalStatus ?? "—"}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Shipping address</CardTitle>
          </CardHeader>
          <CardContent>
            {address.length > 0 ? (
              <address className="text-sm not-italic leading-6">
                {address.map((line) => (
                  <span key={line} className="block">
                    {line}
                  </span>
                ))}
                {order.recipientPhone && (
                  <span className="block text-muted-foreground">
                    {order.recipientPhone}
                  </span>
                )}
              </address>
            ) : (
              <p className="text-sm text-muted-foreground">
                No address on this order.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Buyer</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Name</dt>
                <dd>{order.buyerName ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Country</dt>
                <dd>{order.buyerCountry ?? order.countryCode ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-muted-foreground">Source</dt>
                <dd className="capitalize">{order.source}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Items</CardTitle>
        </CardHeader>
        <CardContent>
          {order.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              The supplier returned no line items for this order.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Item</TableHead>
                    <TableHead className="text-right">Qty</TableHead>
                    <TableHead className="text-right">Unit price</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {order.items.map((item) => (
                    <TableRow key={item.id} data-testid="order-item-row">
                      <TableCell className="max-w-md">
                        <span className="line-clamp-2 font-medium">
                          {item.title ?? item.externalProductId ?? "Untitled item"}
                        </span>
                        {item.skuAttributes && (
                          <span className="text-xs text-muted-foreground">
                            {item.skuAttributes}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {item.quantity}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatMoney(item.unitPrice, item.currency)}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {item.externalStatus ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Shipments</h2>
          <EbayShipOrderForm order={order} />
          <ShopifyShipOrderForm order={order} />
          <WooCommerceShipOrderForm order={order} />
          {order.shipments.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No shipments yet. Tracking appears here once the supplier ships.
            </p>
          ) : (
            order.shipments.map((shipment) => (
              <ShipmentCard key={shipment.id} shipment={shipment} />
            ))
          )}
        </div>

        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Timeline</h2>
          <OrderTimeline orderId={order.id} />
        </div>
      </div>
    </div>
  );
}
