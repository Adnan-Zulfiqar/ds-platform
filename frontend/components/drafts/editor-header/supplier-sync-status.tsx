import { Badge } from "@/components/ui/badge";
import {
  formatSupplierSyncedAt,
  shipToLabel,
} from "@/components/drafts/editor-header/readiness";
import { cn } from "@/lib/utils";

export type SupplierSyncKind =
  | "up_to_date"
  | "stale"
  | "refreshing"
  | "failed"
  | "never";

interface SupplierSyncStatusProps {
  kind: SupplierSyncKind;
  lastSyncedAt: string | null | undefined;
  shipToCountry?: string | null;
  locale?: string | null;
  className?: string;
}

function labelFor(kind: SupplierSyncKind): string {
  switch (kind) {
    case "refreshing":
      return "Refreshing supplier";
    case "failed":
      return "Supplier refresh failed";
    case "stale":
      return "Supplier data stale";
    case "never":
      return "Supplier not synced";
    default:
      return "Supplier up to date";
  }
}

export function SupplierSyncStatus({
  kind,
  lastSyncedAt,
  shipToCountry,
  locale,
  className,
}: SupplierSyncStatusProps) {
  const syncedLabel = formatSupplierSyncedAt(lastSyncedAt, locale);
  const destination = shipToLabel(shipToCountry);

  return (
    <div
      className={cn("flex flex-wrap items-center gap-2", className)}
      data-testid="supplier-sync-status"
    >
      <Badge
        variant={
          kind === "failed"
            ? "destructive"
            : kind === "stale" || kind === "never"
              ? "warning"
              : kind === "refreshing"
                ? "secondary"
                : "success"
        }
      >
        {labelFor(kind)}
      </Badge>
      {syncedLabel ? (
        <span className="text-xs text-muted-foreground">
          Last synced {syncedLabel}
        </span>
      ) : null}
      {destination ? (
        <span className="text-xs text-muted-foreground">
          Ship-to: {destination}
        </span>
      ) : null}
    </div>
  );
}

/** Heuristic: older than 7 days is stale when we have a sync timestamp. */
export function deriveSupplierSyncKind(params: {
  lastSyncedAt: string | null | undefined;
  lastSyncError: string | null | undefined;
  refreshing: boolean;
}): SupplierSyncKind {
  if (params.refreshing) return "refreshing";
  if (params.lastSyncError) return "failed";
  if (!params.lastSyncedAt) return "never";
  const ageMs = Date.now() - new Date(params.lastSyncedAt).getTime();
  if (Number.isFinite(ageMs) && ageMs > 7 * 24 * 60 * 60 * 1000) return "stale";
  return "up_to_date";
}
