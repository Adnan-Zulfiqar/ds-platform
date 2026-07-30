import type { Metadata } from "next";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Dashboard",
};

/**
 * Dashboard.
 *
 * Phase 0 has no metrics to show, because nothing produces metrics yet. Rather
 * than fabricate numbers — which makes a foundation look finished when it is
 * not — this page states what exists and what is still to come.
 */
export default function DashboardPage() {
  const foundation = [
    { name: "API", detail: "FastAPI, versioned at /api/v1", ready: true },
    { name: "Database", detail: "PostgreSQL with Alembic migrations", ready: true },
    { name: "Multi-tenancy", detail: "Enforced in the repository layer", ready: true },
    { name: "Background workers", detail: "Celery configured, no jobs yet", ready: true },
    { name: "Authentication", detail: "Planned for the next phase", ready: false },
    { name: "Marketplace integrations", detail: "Planned for a later phase", ready: false },
  ];

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground">
          Platform foundation status. Feature work begins in the next phase.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {foundation.map((item) => (
          <Card key={item.name}>
            <CardHeader className="flex-row items-start justify-between space-y-0">
              <div className="space-y-1">
                <CardTitle className="text-base">{item.name}</CardTitle>
                <CardDescription>{item.detail}</CardDescription>
              </div>
              {/* Text, not just colour — a status conveyed by hue alone is
                  invisible to colour-blind users. */}
              <Badge variant={item.ready ? "success" : "secondary"}>
                {item.ready ? "Ready" : "Planned"}
              </Badge>
            </CardHeader>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">No data yet</CardTitle>
          <CardDescription>
            Products, orders and analytics appear here once the integration
            phases are implemented.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex h-32 items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
            Awaiting your first connected store
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
