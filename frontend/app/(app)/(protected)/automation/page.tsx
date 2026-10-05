import type { Metadata } from "next";

import { AutomationPanel } from "@/components/automation/automation-panel";
import { AutomationRunLog } from "@/components/automation/automation-run-log";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Automation" };

export default function AutomationPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Automation"
        description="Schedule inventory sync, pricing, and order refresh. Runs execute in the background only."
      />
      <AutomationPanel />
      <AutomationRunLog />
    </div>
  );
}
