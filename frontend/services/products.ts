import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type {
  ListQuery,
  Page,
  Product,
  ProductDetail,
  ProductImportPayload,
  ProductImportRecord,
  ProductOptimizePayload,
  ProductOptimizeResult,
  ProductVersion,
  ProductWorkspaceCounts,
} from "@/types/api";

import { draftKeys } from "@/services/drafts";

/**
 * Product data access.
 *
 * These endpoints now exist, so this module has real fetchers.
 *
 * The query keys keep the hierarchy `services/users.ts` established: identical
 * shapes across services mean cache invalidation behaves predictably instead of
 * each module inventing its own convention.
 */

export const productKeys = {
  all: ["products"] as const,
  lists: () => [...productKeys.all, "list"] as const,
  list: (query: ListQuery) => [...productKeys.lists(), query] as const,
  details: () => [...productKeys.all, "detail"] as const,
  detail: (id: string) => [...productKeys.details(), id] as const,
  imports: () => [...productKeys.all, "imports"] as const,
  importList: (query: ListQuery) => [...productKeys.imports(), query] as const,
  versions: (id: string) => [...productKeys.detail(id), "versions"] as const,
  workspaceCounts: () => [...productKeys.all, "workspace-counts"] as const,
};

async function fetchProducts(query: ListQuery): Promise<Page<Product>> {
  const { data } = await apiClient.get<Page<Product>>("/products", {
    params: query,
  });
  return data;
}

export function useProducts(
  query: ListQuery = {},
): UseQueryResult<Page<Product>> {
  return useQuery({
    queryKey: productKeys.list(query),
    queryFn: () => fetchProducts(query),
  });
}

async function fetchWorkspaceCounts(): Promise<ProductWorkspaceCounts> {
  const { data } = await apiClient.get<ProductWorkspaceCounts>(
    "/products/workspace-counts",
  );
  return data;
}

/** Draft and published counts for sidebar badges. */
export function useProductWorkspaceCounts(): UseQueryResult<ProductWorkspaceCounts> {
  return useQuery({
    queryKey: productKeys.workspaceCounts(),
    queryFn: fetchWorkspaceCounts,
  });
}

async function fetchProduct(id: string): Promise<ProductDetail> {
  const { data } = await apiClient.get<ProductDetail>(`/products/${id}`);
  return data;
}

export function useProduct(id: string): UseQueryResult<ProductDetail> {
  return useQuery({
    queryKey: productKeys.detail(id),
    queryFn: () => fetchProduct(id),
    enabled: Boolean(id),
  });
}

async function fetchImports(
  query: ListQuery,
): Promise<Page<ProductImportRecord>> {
  const { data } = await apiClient.get<Page<ProductImportRecord>>(
    "/products/imports",
    { params: query },
  );
  return data;
}

export function useProductImports(
  query: ListQuery = {},
): UseQueryResult<Page<ProductImportRecord>> {
  return useQuery({
    queryKey: productKeys.importList(query),
    queryFn: () => fetchImports(query),
  });
}

/**
 * Import a product from AliExpress.
 *
 * Invalidates the whole product namespace on success rather than splicing the
 * new item into every cached page. An import can create *or* update a product,
 * and it changes the import history too — reconciling all of that by hand is
 * how a cache starts quietly disagreeing with the server.
 */
export function useImportProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: ProductImportPayload) => {
      const { data } = await apiClient.post<ProductDetail>(
        "/products/import",
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
      void queryClient.invalidateQueries({ queryKey: draftKeys.all });
    },
  });
}

/** Refresh price, stock and variants for a product already imported. */
export function useSyncProduct() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (productId: string) => {
      const { data } = await apiClient.post<ProductDetail>(
        `/products/${productId}/sync`,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: productKeys.all });
      void queryClient.invalidateQueries({ queryKey: draftKeys.all });
    },
  });
}

async function fetchProductVersions(id: string): Promise<Page<ProductVersion>> {
  const { data } = await apiClient.get<Page<ProductVersion>>(
    `/products/${id}/versions`,
    { params: { size: 50 } },
  );
  return data;
}

/** Version history for a product's AI optimisation — newest first. */
export function useProductVersions(
  id: string,
): UseQueryResult<Page<ProductVersion>> {
  return useQuery({
    queryKey: productKeys.versions(id),
    queryFn: () => fetchProductVersions(id),
    enabled: Boolean(id),
  });
}

/**
 * Generate a new AI-optimised title and description for a product.
 *
 * Uses `StubProvider` — no real AI key is configured on this platform yet
 * (Phase 9 stages 1–2), so the result is deterministic, clearly-synthetic
 * text. Invalidates the product's detail and version-history caches, both
 * of which the response changes.
 */
export function useOptimizeProduct(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: ProductOptimizePayload = {}) => {
      const { data } = await apiClient.post<ProductOptimizeResult>(
        `/products/${productId}/optimize`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: productKeys.detail(productId),
      });
      void queryClient.invalidateQueries({
        queryKey: productKeys.versions(productId),
      });
      void queryClient.invalidateQueries({ queryKey: productKeys.lists() });
    },
  });
}

/**
 * Activate a version — including the original — rolling the product back
 * or forward to it.
 */
export function useActivateProductVersion(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (versionId: string) => {
      const { data } = await apiClient.post<ProductDetail>(
        `/products/${productId}/versions/${versionId}/activate`,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: productKeys.detail(productId),
      });
      void queryClient.invalidateQueries({
        queryKey: productKeys.versions(productId),
      });
      void queryClient.invalidateQueries({ queryKey: productKeys.lists() });
    },
  });
}
