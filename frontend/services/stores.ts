import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page } from "@/types/api";

export type StorePlatform =
  | "shopify"
  | "woocommerce"
  | "ebay"
  | "etsy"
  | "tiktok_shop"
  | "manual";

export type StoreStatus =
  | "pending"
  | "connected"
  | "disconnected"
  | "error"
  | "syncing";

export interface Store {
  id: string;
  name: string;
  slug: string;
  platform: StorePlatform;
  status: StoreStatus;
  storefrontUrl: string | null;
  externalStoreId: string | null;
  currency: string;
  currencyLastSyncedAt: string | null;
  timezone: string;
  settings: Record<string, unknown>;
  inventorySyncEnabled: boolean;
  pricingSyncEnabled: boolean;
  orderSyncEnabled: boolean;
  lastSyncAt: string | null;
  lastActivityAt: string | null;
  lastError: string | null;
  healthScore: number;
  createdAt: string;
  updatedAt: string;
}

export interface StoreCurrencyRefreshResult {
  storeId: string;
  currency: string;
  currencyLastSyncedAt: string;
  source: string;
}

export interface StoreStatistics {
  totalStores: number;
  byStatus: Record<string, number>;
  connected: number;
  withErrors: number;
  productCount: number;
  lastActivityAt: string | null;
}

export const storeKeys = {
  all: ["stores"] as const,
  lists: () => [...storeKeys.all, "list"] as const,
  list: (query: ListQuery) => [...storeKeys.lists(), query] as const,
  statistics: () => [...storeKeys.all, "statistics"] as const,
  detail: (id: string) => [...storeKeys.all, "detail", id] as const,
};

export function useStores(query: ListQuery = {}): UseQueryResult<Page<Store>> {
  return useQuery({
    queryKey: storeKeys.list(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<Store>>("/stores", { params: query });
      return data;
    },
  });
}

export function useStoreStatistics(): UseQueryResult<StoreStatistics> {
  return useQuery({
    queryKey: storeKeys.statistics(),
    queryFn: async () => {
      const { data } = await apiClient.get<StoreStatistics>("/stores/statistics");
      return data;
    },
  });
}

export function useRefreshStoreCurrency(storeId: string | null | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      if (!storeId) {
        throw new Error("No destination store selected.");
      }
      const { data } = await apiClient.post<StoreCurrencyRefreshResult>(
        `/stores/${storeId}/currency/refresh`,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: storeKeys.all });
    },
  });
}
