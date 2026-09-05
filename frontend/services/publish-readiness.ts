import {
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ShopifyPublishReadiness } from "@/types/api";

export const publishReadinessKeys = {
  all: ["publish-readiness"] as const,
  detail: (productId: string, storeId: string | null, draftUpdatedAt: string | null) =>
    [
      ...publishReadinessKeys.all,
      productId,
      storeId ?? "none",
      draftUpdatedAt ?? "unknown",
    ] as const,
};

export async function fetchPublishReadiness(params: {
  productId: string;
  storeId: string | null;
  expectedUpdatedAt?: string | null;
}): Promise<ShopifyPublishReadiness> {
  const { data } = await apiClient.post<ShopifyPublishReadiness>(
    "/integrations/shopify/publish-readiness",
    {
      productId: params.productId,
      storeId: params.storeId || null,
      expectedUpdatedAt: params.expectedUpdatedAt ?? null,
    },
  );
  return data;
}

/** Authoritative Shopify publish check for Review & publish. */
export function usePublishReadiness(params: {
  productId: string;
  storeId: string | null;
  draftUpdatedAt: string | null;
  enabled: boolean;
}): UseQueryResult<ShopifyPublishReadiness> {
  return useQuery({
    queryKey: publishReadinessKeys.detail(
      params.productId,
      params.storeId,
      params.draftUpdatedAt,
    ),
    queryFn: () =>
      fetchPublishReadiness({
        productId: params.productId,
        storeId: params.storeId,
        expectedUpdatedAt: params.draftUpdatedAt,
      }),
    enabled: params.enabled && Boolean(params.productId),
    staleTime: 0,
    gcTime: 30_000,
    refetchOnWindowFocus: false,
  });
}

export function invalidatePublishReadiness(
  queryClient: ReturnType<typeof useQueryClient>,
  productId?: string,
) {
  if (productId) {
    void queryClient.invalidateQueries({
      queryKey: [...publishReadinessKeys.all, productId],
    });
    return;
  }
  void queryClient.invalidateQueries({ queryKey: publishReadinessKeys.all });
}
