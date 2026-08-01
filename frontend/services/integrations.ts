import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type {
  AliExpressAuthorization,
  AliExpressConnectPayload,
  AliExpressStatus,
  ShopifyAuthorization,
  ShopifyConnectPayload,
  ShopifyStatus,
} from "@/types/api";

/**
 * Integration data access.
 *
 * Unlike `services/dashboard.ts`, these endpoints **exist**, so this module has
 * real fetchers.
 *
 * The app secret travels one way only: into `connect`, over TLS, once. Nothing
 * here reads it back, because no response carries it.
 */

export const integrationKeys = {
  all: ["integrations"] as const,
  aliexpress: () => [...integrationKeys.all, "aliexpress"] as const,
  aliexpressStatus: () => [...integrationKeys.aliexpress(), "status"] as const,
  shopify: () => [...integrationKeys.all, "shopify"] as const,
  shopifyStatus: () => [...integrationKeys.shopify(), "status"] as const,
};

async function fetchAliExpressStatus(): Promise<AliExpressStatus> {
  const { data } = await apiClient.get<AliExpressStatus>(
    "/integrations/aliexpress/status",
  );
  return data;
}

export function useAliExpressStatus(): UseQueryResult<AliExpressStatus> {
  return useQuery({
    queryKey: integrationKeys.aliexpressStatus(),
    queryFn: fetchAliExpressStatus,
    // Shorter than the global default: the user often arrives here immediately
    // after completing an OAuth redirect, and stale data would show them
    // "disconnected" moments after they connected.
    staleTime: 10_000,
  });
}

/**
 * Begin a connection.
 *
 * Returns the URL to navigate to. Navigation is left to the caller rather than
 * performed here, because a service module that redirects the browser is
 * impossible to use from anywhere else — a test, a different flow, a retry.
 */
export function useConnectAliExpress() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (
      payload: AliExpressConnectPayload,
    ): Promise<AliExpressAuthorization> => {
      const { data } = await apiClient.post<AliExpressAuthorization>(
        "/integrations/aliexpress/connect",
        payload,
      );
      return data;
    },
    onSuccess: () => {
      // The connection now exists in `pending`, so the cached status is stale.
      void queryClient.invalidateQueries({
        queryKey: integrationKeys.aliexpressStatus(),
      });
    },
  });
}

export function useDisconnectAliExpress() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (): Promise<void> => {
      await apiClient.delete("/integrations/aliexpress/disconnect");
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: integrationKeys.aliexpressStatus(),
      });
    },
  });
}

async function fetchShopifyStatus(): Promise<ShopifyStatus> {
  const { data } = await apiClient.get<ShopifyStatus>("/integrations/shopify/status");
  return data;
}

export function useShopifyStatus(): UseQueryResult<ShopifyStatus> {
  return useQuery({
    queryKey: integrationKeys.shopifyStatus(),
    queryFn: fetchShopifyStatus,
    staleTime: 10_000,
  });
}

export function useConnectShopify() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (
      payload: ShopifyConnectPayload,
    ): Promise<ShopifyAuthorization> => {
      const { data } = await apiClient.post<ShopifyAuthorization>(
        "/integrations/shopify/connect",
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: integrationKeys.shopifyStatus(),
      });
    },
  });
}

export function useDisconnectShopify() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (storeId: string): Promise<void> => {
      await apiClient.delete(`/integrations/shopify/stores/${storeId}`);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: integrationKeys.shopifyStatus(),
      });
    },
  });
}
