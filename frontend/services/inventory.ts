import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page } from "@/types/api";

export interface InventoryProduct {
  id: string;
  title: string;
  externalId: string;
  storeId: string | null;
  stockQuantity: number;
  sellPrice: string | null;
  costPriceMin: string | null;
  currency: string | null;
  lastSyncedAt: string | null;
  lastSyncError: string | null;
  status: string;
}

export interface InventorySyncRun {
  id: string;
  storeId: string | null;
  productId: string | null;
  trigger: string;
  status: string;
  productsSeen: number;
  productsChanged: number;
  errorMessage: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
}

export interface InventoryChange {
  id: string;
  syncRunId: string | null;
  productId: string;
  variantId: string | null;
  storeId: string | null;
  previousQuantity: number;
  newQuantity: number;
  reason: string;
  note: string | null;
  createdAt: string;
}

export const inventoryKeys = {
  all: ["inventory"] as const,
  list: (query: ListQuery) => [...inventoryKeys.all, "list", query] as const,
  runs: (query: ListQuery) => [...inventoryKeys.all, "runs", query] as const,
  changes: (query: ListQuery) => [...inventoryKeys.all, "changes", query] as const,
};

/** `GET /inventory/sync-runs`: did the sync run, and what did it find. */
export function useInventorySyncRuns(
  query: ListQuery = {},
): UseQueryResult<Page<InventorySyncRun>> {
  return useQuery({
    queryKey: inventoryKeys.runs(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<InventorySyncRun>>("/inventory/sync-runs", {
        params: query,
      });
      return data;
    },
  });
}

/** `GET /inventory/changes`: every quantity movement, newest first. */
export function useInventoryChanges(
  query: ListQuery = {},
): UseQueryResult<Page<InventoryChange>> {
  return useQuery({
    queryKey: inventoryKeys.changes(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<InventoryChange>>("/inventory/changes", {
        params: query,
      });
      return data;
    },
  });
}

export function useInventory(
  query: ListQuery = {},
): UseQueryResult<Page<InventoryProduct>> {
  return useQuery({
    queryKey: inventoryKeys.list(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<InventoryProduct>>("/inventory", {
        params: query,
      });
      return data;
    },
  });
}

export function useSyncInventory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { storeId?: string; productId?: string } = {}) => {
      const { data } = await apiClient.post<InventorySyncRun>("/inventory/sync", payload);
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: inventoryKeys.all });
    },
  });
}
