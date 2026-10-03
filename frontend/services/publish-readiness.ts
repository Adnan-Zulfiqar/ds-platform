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

/** Channels with a server publish check (EBAY-C3 added eBay). */
export type PublishChannel = "shopify" | "ebay";

export async function fetchPublishReadiness(params: {
  productId: string;
  storeId: string | null;
  expectedUpdatedAt?: string | null;
  channel?: PublishChannel;
}): Promise<ShopifyPublishReadiness> {
  const { data } = await apiClient.post<ShopifyPublishReadiness>(
    `/integrations/${params.channel ?? "shopify"}/publish-readiness`,
    {
      productId: params.productId,
      storeId: params.storeId || null,
      expectedUpdatedAt: params.expectedUpdatedAt ?? null,
    },
  );
  return data;
}

/** Authoritative publish check for Review & publish, for the chosen store's channel. */
export function usePublishReadiness(params: {
  productId: string;
  storeId: string | null;
  draftUpdatedAt: string | null;
  enabled: boolean;
  channel?: PublishChannel;
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
        channel: params.channel,
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
