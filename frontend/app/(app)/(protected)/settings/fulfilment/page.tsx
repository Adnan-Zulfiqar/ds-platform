import type { Metadata } from "next";

import { FulfilmentSettingsForm } from "@/components/orders/fulfilment-settings-form";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Fulfilment" };

/** Settings → Fulfilment (Track F). The `(protected)` layout guards it. */
export default function FulfilmentSettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Fulfilment"
        description="Ordering from AliExpress and sending tracking to your stores."
      />
      <FulfilmentSettingsForm />
    </div>
  );
}
