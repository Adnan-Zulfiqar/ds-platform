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
} from "@/types/api";

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
    },
  });
}
