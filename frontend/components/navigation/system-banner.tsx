"use client";

import { AlertTriangle, Info, Wrench } from "lucide-react";

import { cn } from "@/lib/utils";
import { useSystemStatus } from "@/services/system";

/**
 * Banners from DropPilot operators (D-019): maintenance mode, while changes
 * are paused, and live announcements. Silent when there is nothing to say,
 * and when the status cannot be read: a missing banner is better than an
 * error box on every page.
 */
export function SystemBanner() {
  const status = useSystemStatus();
  if (!status.data) return null;
  const { maintenance, maintenanceMessage, announcements } = status.data;
  if (!maintenance && announcements.length === 0) return null;

  return (
    <div className="space-y-px" role="region" aria-label="Platform notices">
      {maintenance && (
        <div
          role="alert"
          className="flex items-start gap-2 bg-amber-100 px-4 py-2 text-sm text-amber-950 dark:bg-amber-950 dark:text-amber-100"
          data-testid="system-maintenance"
        >
          <Wrench className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>
            <strong>Maintenance:</strong> changes are paused for now; you can
            still view everything.
            {maintenanceMessage ? ` ${maintenanceMessage}` : ""}
          </span>
        </div>
      )}
      {announcements.map((a) => (
        <div
          key={a.id}
          role={a.level === "critical" ? "alert" : "status"}
          className={cn(
            "flex items-start gap-2 px-4 py-2 text-sm",
            a.level === "critical"
              ? "bg-destructive text-destructive-foreground"
              : a.level === "warning"
                ? "bg-amber-50 text-amber-950 dark:bg-amber-950/60 dark:text-amber-100"
                : "bg-primary/10 text-foreground",
          )}
          data-testid="system-announcement"
        >
          {a.level === "info" ? (
            <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          ) : (
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          )}
          <span>
            <strong>{a.title}</strong>
            {a.body ? ` ${a.body}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}
