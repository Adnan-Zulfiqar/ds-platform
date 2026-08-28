import type { Metadata } from "next";

import { ComingSoon } from "@/components/ui/coming-soon";

export const metadata: Metadata = { title: "Customers" };

export default function CustomersPage() {
  return (
    <ComingSoon
      title="Customers"
      description="Customer profiles and purchase history."
      planned={[
        "Buyer profiles linked to synchronised orders",
        "Purchase history and lifetime value",
        "Contact and shipping address book",
      ]}
    />
  );
}
