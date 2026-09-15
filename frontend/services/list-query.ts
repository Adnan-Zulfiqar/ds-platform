import type { ListQuery } from "@/types/api";

/**
 * Translate a `ListQuery` into the wire parameter names.
 *
 * The backend's shared list dependency reads `sort_by` / `sort_dir` as plain
 * query parameters — FastAPI function arguments, not schema fields — so the
 * camelCase alias generator that handles request and response bodies never
 * sees them. A service that forwards `{ sortBy }` unchanged is silently
 * ignored by the server and gets the default order back. `orders.ts` carried
 * this translation privately; it lives here so every list service sends the
 * same names.
 */
export function toListParams(query: ListQuery): Record<string, string | number> {
  const { sortBy, sortDir, ...rest } = query;
  const params: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(rest)) {
    if (value !== undefined && value !== "") params[key] = value;
  }
  if (sortBy) params.sort_by = sortBy;
  if (sortDir) params.sort_dir = sortDir;
  return params;
}
