import type { Metadata } from "next";

import { TeamWorkspace } from "@/components/team/team-workspace";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Team" };

/** Settings → Team (Track E4). The `(protected)` layout guards it. */
export default function TeamSettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader title="Team" description="Invite colleagues and see who has access." />
      <TeamWorkspace />
    </div>
  );
}
