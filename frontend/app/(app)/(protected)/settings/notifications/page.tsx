import type { Metadata } from "next";

import { EmailPreferencesForm } from "@/components/notifications/email-preferences";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Notifications" };

/** Settings → Notifications (Track E3). The `(protected)` layout guards it. */
export default function NotificationSettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Notifications"
        description="Choose which notifications you also get by email."
      />
      <EmailPreferencesForm />
    </div>
  );
}
