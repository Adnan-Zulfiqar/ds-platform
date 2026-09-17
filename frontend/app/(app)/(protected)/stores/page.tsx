import type { Metadata } from "next";
import Link from "next/link";
import { Plug } from "lucide-react";

import { StoreStatisticsCards } from "@/components/stores/store-statistics-cards";
import { StoreTable } from "@/components/stores/store-table";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Stores" };

/**
 * Store records — the supporting view behind Integrations (UX-L2D-06).
 *
 * Integrations is where a store is authorized, repaired or disconnected;
 * this page lists every store record the workspace has, including ones that
 * were disconnected (a disconnect deletes the provider connection but keeps
 * the store row, its products and its listings). The manual "Add store"
 * action that used to sit here is gone: it created a store row with no
 * authorization, which then appeared in the editor's store list and could
 * never publish.
 */
export default function StoresPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Stores"
        description="Every store this workspace has known, with its last sync and activity. Connect, repair or disconnect stores under Integrations."
        actions={
          <Button asChild variant="outline" className="min-h-11 sm:min-h-9">
            <Link href="/settings/integrations">
              <Plug className="mr-2 h-4 w-4" aria-hidden="true" />
              Manage connections
            </Link>
          </Button>
        }
      />
      <StoreStatisticsCards />
      <StoreTable />
    </div>
  );
}
