import type { Metadata } from "next";

import { ComingSoon } from "@/components/ui/coming-soon";

export const metadata: Metadata = { title: "Settings" };

/**
 * Settings has no editable fields yet.
 *
 * The account details it would show — name, email, tenant, role — are already
 * visible in the user menu. Repeating them here as read-only text would look
 * like a form that silently refuses to save, which is worse than an honest
 * placeholder.
 */
export default function SettingsPage() {
  return (
    <ComingSoon
      title="Settings"
      description="Workspace, team, and billing configuration."
      planned={[
        "Profile and password management",
        "Team invitations and role assignment",
        "Workspace name, timezone, and currency",
        "Subscription and billing",
        "API keys and webhooks",
      ]}
    />
  );
}
