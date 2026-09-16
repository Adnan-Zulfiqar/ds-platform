export type SupplierSyncKind =
  | "up_to_date"
  | "stale"
  | "refreshing"
  | "failed"
  | "never";

/**
 * How current the supplier snapshot is. The header shows a single warning
 * line for `stale` / `failed` / `never`; the badge component that used to
 * live beside this function had no consumer and was removed in UX-L2D-07.
 *
 * Heuristic: older than 7 days is stale when we have a sync timestamp.
 */
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
