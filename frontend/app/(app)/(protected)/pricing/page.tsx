import type { Metadata } from "next";

import { PricingActions } from "@/components/pricing/pricing-actions";
import { PricingRulesPanel } from "@/components/pricing/pricing-rules-panel";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Pricing" };

export default function PricingPage() {
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Pricing"
        description="Markup rules with preview before apply. Every change is audited."
        actions={<PricingActions />}
      />
      <PricingRulesPanel />
    </div>
  );
}
