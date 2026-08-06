import {
  useQuery,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page, Product, ProductDetail } from "@/types/api";

/**
 * Draft product data access (Product Workspace V2).
 *
 * Drafts are the same product aggregate as Products, filtered server-side to
 * rows without a synced channel listing. Query keys stay under a dedicated
 * namespace so invalidating drafts cannot thrash the published Products cache.
 */

export const draftKeys = {
  all: ["drafts"] as const,
  lists: () => [...draftKeys.all, "list"] as const,
  list: (query: ListQuery) => [...draftKeys.lists(), query] as const,
  details: () => [...draftKeys.all, "detail"] as const,
  detail: (id: string) => [...draftKeys.details(), id] as const,
};

async function fetchDrafts(query: ListQuery): Promise<Page<Product>> {
  const { data } = await apiClient.get<Page<Product>>("/drafts", {
    params: query,
  });
  return data;
}

export function useDrafts(
  query: ListQuery = {},
): UseQueryResult<Page<Product>> {
  return useQuery({
    queryKey: draftKeys.list(query),
    queryFn: () => fetchDrafts(query),
  });
}

async function fetchDraft(id: string): Promise<ProductDetail> {
  const { data } = await apiClient.get<ProductDetail>(`/drafts/${id}`);
  return data;
}

export function useDraft(id: string): UseQueryResult<ProductDetail> {
  return useQuery({
    queryKey: draftKeys.detail(id),
    queryFn: () => fetchDraft(id),
    enabled: Boolean(id),
  });
}
