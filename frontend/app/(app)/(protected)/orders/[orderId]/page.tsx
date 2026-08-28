import type { Metadata } from "next";

import { OrderDetailView } from "@/components/orders/order-detail-view";

export const metadata: Metadata = { title: "Order details" };

/**
 * One order: summary, address, items, shipments with tracking, and the
 * timeline. The Server Component only unwraps the route param; everything
 * below is a client island that owns its own fetching.
 */
export default async function OrderDetailPage({
  params,
}: {
  params: Promise<{ orderId: string }>;
}) {
  const { orderId } = await params;
  return <OrderDetailView orderId={orderId} />;
}
