import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type {
  DraftPricingApplyPayload,
  DraftPricingWorkspace,
  ListQuery,
  Page,
  Product,
  ProductDetail,
  ProductUpdatePayload,
} from "@/types/api";

/**
 * Draft product data access (Product Workspace / Draft Editor).
 *
 * Drafts are the same product aggregate as Products, filtered server-side to
 * rows without a synced channel listing.
 */

export const draftKeys = {
  all: ["drafts"] as const,
  lists: () => [...draftKeys.all, "list"] as const,
  list: (query: ListQuery) => [...draftKeys.lists(), query] as const,
  details: () => [...draftKeys.all, "detail"] as const,
  detail: (id: string) => [...draftKeys.details(), id] as const,
  pricing: (id: string) => [...draftKeys.all, "pricing", id] as const,
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

function invalidateDraftWorkspace(
  queryClient: ReturnType<typeof useQueryClient>,
  productId?: string,
) {
  // String keys avoid a circular import with `services/products`.
  void queryClient.invalidateQueries({ queryKey: draftKeys.all });
  void queryClient.invalidateQueries({ queryKey: ["products"] });
  if (productId) {
    void queryClient.invalidateQueries({
      queryKey: draftKeys.detail(productId),
    });
  }
}

/** PATCH merchant-editable fields on a draft (Save Draft). */
export function useUpdateDraft(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: ProductUpdatePayload) => {
      const { data } = await apiClient.patch<ProductDetail>(
        `/drafts/${productId}`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      invalidateDraftWorkspace(queryClient, productId);
    },
  });
}

/** Refresh supplier snapshot for a draft (cost/stock/variants). */
export function useRefreshDraft(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<ProductDetail>(
        `/drafts/${productId}/refresh`,
      );
      return data;
    },
    onSuccess: () => {
      invalidateDraftWorkspace(queryClient, productId);
    },
  });
}

export function useReorderDraftImages(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (imageIds: string[]) => {
      const { data } = await apiClient.patch<ProductDetail>(
        `/drafts/${productId}/images/reorder`,
        { imageIds },
      );
      return data;
    },
    onSuccess: () => invalidateDraftWorkspace(queryClient, productId),
  });
}

export function useAddDraftImage(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { url: string; altText?: string | null }) => {
      const { data } = await apiClient.post<ProductDetail>(
        `/drafts/${productId}/images`,
        payload,
      );
      return data;
    },
    onSuccess: () => invalidateDraftWorkspace(queryClient, productId),
  });
}

export function useUpdateDraftImage(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      imageId,
      altText,
    }: {
      imageId: string;
      altText: string | null;
    }) => {
      const { data } = await apiClient.patch<ProductDetail>(
        `/drafts/${productId}/images/${imageId}`,
        { altText },
      );
      return data;
    },
    onSuccess: () => invalidateDraftWorkspace(queryClient, productId),
  });
}

export function useRemoveDraftImage(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (imageId: string) => {
      const { data } = await apiClient.delete<ProductDetail>(
        `/drafts/${productId}/images/${imageId}`,
      );
      return data;
    },
    onSuccess: () => invalidateDraftWorkspace(queryClient, productId),
  });
}

export function useUpdateDraftVariant(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      variantId,
      ...payload
    }: {
      variantId: string;
      label?: string | null;
      merchantSku?: string | null;
      sellPrice?: string | null;
      compareAtPrice?: string | null;
      imageUrl?: string | null;
      isEnabled?: boolean;
    }) => {
      const { data } = await apiClient.patch<ProductDetail>(
        `/drafts/${productId}/variants/${variantId}`,
        payload,
      );
      return data;
    },
    onSuccess: () => invalidateDraftWorkspace(queryClient, productId),
  });
}

export function useDraftPricing(
  productId: string,
): UseQueryResult<DraftPricingWorkspace> {
  return useQuery({
    queryKey: draftKeys.pricing(productId),
    queryFn: async () => {
      const { data } = await apiClient.get<DraftPricingWorkspace>(
        `/drafts/${productId}/pricing`,
      );
      return data;
    },
    enabled: Boolean(productId),
  });
}

export function usePreviewDraftPricing(productId: string) {
  return useMutation({
    mutationFn: async (payload: DraftPricingApplyPayload) => {
      const { data } = await apiClient.post<DraftPricingWorkspace>(
        `/drafts/${productId}/pricing/preview`,
        payload,
      );
      return data;
    },
  });
}

export function useApplyDraftPricing(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: DraftPricingApplyPayload) => {
      const { data } = await apiClient.post<DraftPricingWorkspace>(
        `/drafts/${productId}/pricing/apply`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      invalidateDraftWorkspace(queryClient, productId);
      void queryClient.invalidateQueries({
        queryKey: draftKeys.pricing(productId),
      });
    },
  });
}
