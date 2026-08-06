import type { Metadata } from "next";

import { ImportHistoryTable } from "@/components/products/import-history-table";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Import History" };

/**
 * Supplier import attempts — successes, failures, and in-progress jobs.
 */
export default function ImportHistoryPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Import History"
        description="Every Import as Draft attempt for this workspace, including failures."
      />
      <ImportHistoryTable />
    </div>
  );
}
