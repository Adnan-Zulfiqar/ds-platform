import type { Metadata } from "next";

import { ComingSoon } from "@/components/ui/coming-soon";

export const metadata: Metadata = { title: "Orders" };

export default function OrdersPage() {
  return (
    <ComingSoon
      title="Orders"
      description="Track, fulfil, and monitor customer orders."
      planned={[
        "Unified order list across every connected store",
        "Automatic supplier fulfilment",
        "Shipment tracking and customer notifications",
        "Refund and cancellation handling",
      ]}
    />
  );
}
