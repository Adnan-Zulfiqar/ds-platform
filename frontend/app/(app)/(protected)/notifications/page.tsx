import type { Metadata } from "next";

import { NotificationsPanel } from "@/components/notifications/notifications-panel";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Notifications" };

export default function NotificationsPage() {
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Notifications"
        description="Import, sync, pricing, shipment, and automation alerts."
      />
      <NotificationsPanel />
    </div>
  );
}
