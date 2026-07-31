import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type {
  Order,
  OrderDetail,
  OrderListQuery,
  OrderStatistics,
  OrderSyncPayload,
  OrderSyncRun,
  OrderTimelineEntry,
  Page,
} from "@/types/api";

/**
 * Order data access.
 *
 * Query keys follow the hierarchy `services/products.ts` established, so cache
 * invalidation behaves the same way across modules: invalidating
 * `orderKeys.all` after a sync clears the list, every detail, every timeline,
 * and the statistics in one call.
 */

export const orderKeys = {
  all: ["orders"] as const,
  lists: () => [...orderKeys.all, "list"] as const,
  list: (query: OrderListQuery) => [...orderKeys.lists(), query] as const,
  details: () => [...orderKeys.all, "detail"] as const,
  detail: (id: string) => [...orderKeys.details(), id] as const,
  timeline: (id: string) => [...orderKeys.all, "timeline", id] as const,
  statistics: () => [...orderKeys.all, "statistics"] as const,
};

/**
 * Translate the query object into the wire parameter names.
 *
 * The backend's shared list dependency reads `sort_by` / `sort_dir` as plain
 * query parameters (they are FastAPI function arguments, not schema fields, so
 * the camelCase alias generator never sees them). Everything else passes
 * through unchanged.
 */
function toWireParams(query: OrderListQuery): Record<string, string | number> {
  const { sortBy, sortDir, ...rest } = query;
  const params: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(rest)) {
    if (value !== undefined && value !== "") params[key] = value;
  }
  if (sortBy) params.sort_by = sortBy;
  if (sortDir) params.sort_dir = sortDir;
  return params;
}

async function fetchOrders(query: OrderListQuery): Promise<Page<Order>> {
  const { data } = await apiClient.get<Page<Order>>("/orders", {
    params: toWireParams(query),
  });
  return data;
}

export function useOrders(
  query: OrderListQuery = {},
): UseQueryResult<Page<Order>> {
  return useQuery({
    queryKey: orderKeys.list(query),
    queryFn: () => fetchOrders(query),
  });
}

async function fetchOrder(id: string): Promise<OrderDetail> {
  const { data } = await apiClient.get<OrderDetail>(`/orders/${id}`);
  return data;
}

export function useOrder(id: string): UseQueryResult<OrderDetail> {
  return useQuery({
    queryKey: orderKeys.detail(id),
    queryFn: () => fetchOrder(id),
    enabled: Boolean(id),
  });
}

async function fetchTimeline(id: string): Promise<OrderTimelineEntry[]> {
  const { data } = await apiClient.get<OrderTimelineEntry[]>(
    `/orders/${id}/timeline`,
  );
  return data;
}

export function useOrderTimeline(
  id: string,
): UseQueryResult<OrderTimelineEntry[]> {
  return useQuery({
    queryKey: orderKeys.timeline(id),
    queryFn: () => fetchTimeline(id),
    enabled: Boolean(id),
  });
}

async function fetchStatistics(): Promise<OrderStatistics> {
  const { data } = await apiClient.get<OrderStatistics>("/orders/statistics");
  return data;
}

export function useOrderStatistics(): UseQueryResult<OrderStatistics> {
  return useQuery({
    queryKey: orderKeys.statistics(),
    queryFn: fetchStatistics,
  });
}

/**
 * Run one incremental synchronisation.
 *
 * Invalidates the whole order namespace on success rather than reconciling
 * pages by hand — a sync can create, update, and re-status orders all at once,
 * which is exactly the situation where a hand-spliced cache starts quietly
 * disagreeing with the server.
 */
export function useSyncOrders() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: OrderSyncPayload = {}) => {
      const { data } = await apiClient.post<OrderSyncRun>(
        "/orders/sync",
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: orderKeys.all });
    },
  });
}
