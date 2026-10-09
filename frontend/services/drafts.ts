import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import { toListParams } from "@/services/list-query";
import type {
  DraftPricingApplyPayload,
  DraftPricingWorkspace,
  ListQuery,
  Page,
  Product,
  ProductDetail,
  ProductUpdatePayload,
  SeoScore,
  ShopifyPublishResult,
  StoreListing,
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
  listings: (id: string) => [...draftKeys.all, "listings", id] as const,
  seoScore: (id: string) => [...draftKeys.all, "seo-score", id] as const,
};

async function fetchDrafts(query: ListQuery): Promise<Page<Product>> {
  const { data } = await apiClient.get<Page<Product>>("/drafts", {
    params: toListParams(query),
  });
  return data;
}

export function useDrafts(
  query: ListQuery = {},
): UseQueryResult<Page<Product>> {
  return useQuery({
    queryKey: draftKeys.list(query),
    queryFn: () => fetchDrafts(query),
    // A search, sort or page change keeps the current rows on screen until
    // the next page arrives instead of collapsing the table to a skeleton.
    placeholderData: keepPreviousData,
  });
}

/**
 * Exported for the editor's conflict path, which must read the server's
 * copy without touching the query cache (a cached write could disturb the
 * draft the merchant is still editing).
 */
export async function fetchDraft(id: string): Promise<ProductDetail> {
  const { data } = await apiClient.get<ProductDetail>(`/drafts/${id}`);
  return data;
}

export interface PublishDraftPayload {
  productId: string;
  storeId: string;
  /** The version the merchant saw; the server refuses a stale one (UX-L2B). */
  expectedUpdatedAt: string;
  replaceAiContent?: boolean;
}

/**
 * Publish a saved draft to one channel's store. A plain function, not a
 * mutation hook: the editor sequences it after its own save and owns the
 * result state, and nothing in the query cache describes a publish.
 */
export async function publishDraft(
  channel: "shopify" | "ebay" | "woocommerce",
  payload: PublishDraftPayload,
): Promise<ShopifyPublishResult> {
  const { data } = await apiClient.post<ShopifyPublishResult>(
    `/integrations/${channel}/publish`,
    payload,
  );
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

/** Switch every variant of a draft (or the listed ones) on or off in one
 * request: a draft can carry hundreds of variants. */
export function useSetDraftVariantsEnabled(productId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { enabled: boolean; variantIds?: string[] }) => {
      const { data } = await apiClient.patch<ProductDetail>(
        `/drafts/${productId}/variants`,
        input,
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

export function useDraftListings(
  productId: string,
): UseQueryResult<StoreListing[]> {
  return useQuery({
    queryKey: draftKeys.listings(productId),
    queryFn: async () => {
      const { data } = await apiClient.get<StoreListing[]>(
        `/drafts/${productId}/listings`,
      );
      return data;
    },
    enabled: Boolean(productId),
    // Listing status is shown as "unavailable" with an explicit Try again
    // when this fails; silent retries would delay that truth and hide the
    // failure behind a spinner (selectively integrated from the reviewed
    // historical UX-L2C change, UX-L2D-GATE-04).
    retry: false,
    // Show cached status at once, then confirm it against the server.
    refetchOnMount: "always",
  });
}

export function useDraftSeoScore(
  productId: string,
): UseQueryResult<SeoScore> {
  return useQuery({
    queryKey: draftKeys.seoScore(productId),
    queryFn: async () => {
      const { data } = await apiClient.get<SeoScore>(
        `/drafts/${productId}/seo-score`,
      );
      return data;
    },
    enabled: Boolean(productId),
  });
}
