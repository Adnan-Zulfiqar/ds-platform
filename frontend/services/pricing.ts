import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page } from "@/types/api";

export type PricingScope = "global" | "store" | "category" | "product";
export type PricingStrategy = "percentage_markup" | "fixed_markup" | "tiered";

export interface PricingRule {
  id: string;
  name: string;
  scope: PricingScope;
  strategy: PricingStrategy;
  storeId: string | null;
  categoryId: string | null;
  productId: string | null;
  markupPercent: string | null;
  markupFixed: string | null;
  minProfit: string | null;
  maxPrice: string | null;
  tiers: Array<{ minCost?: string; maxCost?: string; markupPercent?: string }>;
  currency: string | null;
  priority: number;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface PricingPreviewItem {
  productId: string;
  title: string;
  costPrice: string | null;
  currentSellPrice: string | null;
  proposedSellPrice: string | null;
  ruleId: string | null;
  ruleName: string | null;
  currency: string | null;
}

export interface PricingPreview {
  items: PricingPreviewItem[];
  wouldChange: number;
}

export interface PriceChange {
  id: string;
  productId: string;
  previousPrice: string | null;
  newPrice: string | null;
  costPrice: string | null;
  currency: string | null;
  reason: string;
  appliedAt: string;
}

export interface PricingRuleCreatePayload {
  name: string;
  scope: PricingScope;
  strategy: PricingStrategy;
  storeId?: string;
  categoryId?: string;
  productId?: string;
  markupPercent?: string;
  markupFixed?: string;
  minProfit?: string;
  maxPrice?: string;
  tiers?: Array<{ minCost?: string; maxCost?: string; markupPercent?: string }>;
  currency?: string;
  priority?: number;
  isActive?: boolean;
}

export const pricingKeys = {
  all: ["pricing"] as const,
  list: (query: ListQuery) => [...pricingKeys.all, "list", query] as const,
  changes: (query: ListQuery) => [...pricingKeys.all, "changes", query] as const,
};

/** `GET /pricing/changes`: the audit trail, which had no screen. */
export function usePriceChanges(query: ListQuery = {}): UseQueryResult<Page<PriceChange>> {
  return useQuery({
    queryKey: pricingKeys.changes(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<PriceChange>>("/pricing/changes", {
        params: query,
      });
      return data;
    },
  });
}

export function usePricingRules(
  query: ListQuery = {},
): UseQueryResult<Page<PricingRule>> {
  return useQuery({
    queryKey: pricingKeys.list(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<PricingRule>>("/pricing/rules", {
        params: query,
      });
      return data;
    },
  });
}

export function useCreatePricingRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PricingRuleCreatePayload) => {
      const { data } = await apiClient.post<PricingRule>("/pricing/rules", payload);
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: pricingKeys.all });
    },
  });
}

export interface PricingRuleUpdatePayload {
  name?: string;
  priority?: number;
  markupPercent?: string | null;
  markupFixed?: string | null;
  minProfit?: string | null;
  maxPrice?: string | null;
  isActive?: boolean;
}

/** `PATCH /pricing/rules/{id}`: the endpoint existed without a UI. */
export function useUpdatePricingRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: PricingRuleUpdatePayload & { id: string }) => {
      const { data } = await apiClient.patch<PricingRule>(`/pricing/rules/${id}`, payload);
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: pricingKeys.all });
    },
  });
}

export function useDeletePricingRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/pricing/rules/${id}`);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: pricingKeys.all });
    },
  });
}

export function usePreviewPricing() {
  return useMutation({
    mutationFn: async (payload: { storeId?: string; limit?: number } = {}) => {
      const { data } = await apiClient.post<PricingPreview>(
        "/pricing/preview",
        payload,
      );
      return data;
    },
  });
}

export function useApplyPricing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { storeId?: string; limit?: number } = {}) => {
      const { data } = await apiClient.post<PriceChange[]>("/pricing/apply", payload);
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: pricingKeys.all });
    },
  });
}
