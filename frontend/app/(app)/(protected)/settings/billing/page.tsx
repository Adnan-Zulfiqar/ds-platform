import type { Metadata } from "next";

import { BillingWorkspace } from "@/components/billing/billing-workspace";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Billing" };

/** Settings → Billing (Track E6c). The `(protected)` layout guards it. */
export default async function BillingSettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ checkout?: string }>;
}) {
  const { checkout } = await searchParams;
  return (
    <div className="space-y-6">
      <PageHeader title="Billing" description="Your plan, listing usage and payment method." />
      <BillingWorkspace checkout={checkout ?? null} />
    </div>
  );
}
