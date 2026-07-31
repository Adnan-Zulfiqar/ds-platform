import type { Metadata } from "next";

import { ComingSoon } from "@/components/ui/coming-soon";

export const metadata: Metadata = { title: "Analytics" };

/**
 * Distinct from the dashboard: the dashboard answers "how are things right
 * now", analytics answers "why, and over what period". They are different
 * products and should not be merged.
 */
export default function AnalyticsPage() {
  return (
    <ComingSoon
      title="Analytics"
      description="Revenue, profit, and performance reporting."
      planned={[
        "Revenue and profit trends with period comparison",
        "Product and supplier performance breakdowns",
        "Channel profitability after fees and shipping",
        "Exportable reports",
      ]}
    />
  );
}
