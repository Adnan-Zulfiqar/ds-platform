import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type {
  AliExpressAuthorization,
  AliExpressStatus,
  EbayAuthorization,
  EbayStatus,
  ShopifyAuthorization,
  ShopifyConnectPayload,
  ShopifyStatus,
  ShopifyWebhookReconcileResult,
} from "@/types/api";

/**
 * Integration data access.
 *
 * Unlike `services/dashboard.ts`, these endpoints **exist**, so this module has
 * real fetchers.
 *
 * AliExpress connect sends no app credentials — the platform owns
 * ``ALIEXPRESS_APP_*``. Seller tokens never appear in API responses.
 */

export const integrationKeys = {
  all: ["integrations"] as const,
  aliexpress: () => [...integrationKeys.all, "aliexpress"] as const,
  aliexpressStatus: () => [...integrationKeys.aliexpress(), "status"] as const,
  shopify: () => [...integrationKeys.all, "shopify"] as const,
  shopifyStatus: () => [...integrationKeys.shopify(), "status"] as const,
  ebay: () => [...integrationKeys.all, "ebay"] as const,
  ebayStatus: () => [...integrationKeys.ebay(), "status"] as const,
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
    mutationFn: async (): Promise<AliExpressAuthorization> => {
      const { data } = await apiClient.post<AliExpressAuthorization>(
        "/integrations/aliexpress/connect",
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

export function useClaimShopifyInstall() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: {
      installToken: string;
      storeName?: string;
    }): Promise<ShopifyAuthorization> => {
      const { data } = await apiClient.post<ShopifyAuthorization>(
        "/integrations/shopify/claim-install",
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

/**
 * Retry webhook registration for a store that is connected but degraded.
 *
 * Deliberately a mutation with no retry and no automatic invocation: the server
 * side is idempotent, but a client that fires this on render — or retries it on
 * failure — turns one merchant click into a loop of listings against Shopify.
 * The button is the only trigger.
 *
 * The status query is invalidated on settle rather than on success, because a
 * *degraded* result is still a result the card must re-read: the authoritative
 * `webhooksRegisteredAt` may have changed even when the report is unhealthy.
 */
export function useReconcileShopifyWebhooks() {
  const queryClient = useQueryClient();

  return useMutation({
    retry: false,
    mutationFn: async (storeId: string): Promise<ShopifyWebhookReconcileResult> => {
      const { data } = await apiClient.post<ShopifyWebhookReconcileResult>(
        `/integrations/shopify/stores/${storeId}/webhooks/reconcile`,
      );
      return data;
    },
    onSettled: () => {
      void queryClient.invalidateQueries({
        queryKey: integrationKeys.shopifyStatus(),
      });
    },
  });
}

/**
 * eBay seller connection.
 *
 * The same shape as AliExpress rather than a second style: status is a query,
 * connect and disconnect are mutations, and the redirect is left to the caller.
 *
 * No eBay credential is ever sent from the browser. The client id, certificate
 * id and RuName live in server environment configuration; the merchant only
 * approves access for their own seller account on eBay's own page.
 */
async function fetchEbayStatus(): Promise<EbayStatus> {
  const { data } = await apiClient.get<EbayStatus>("/integrations/ebay/status");
  return data;
}

export function useEbayStatus(): UseQueryResult<EbayStatus> {
  return useQuery({
    queryKey: integrationKeys.ebayStatus(),
    queryFn: fetchEbayStatus,
    // Shorter than the global default, matching the other integrations: the
    // merchant usually arrives here straight off an eBay redirect, and stale
    // data would tell them "not connected" seconds after they connected.
    staleTime: 10_000,
  });
}

/**
 * Begin a connection, returning the eBay consent URL.
 *
 * Navigation is the caller's job. A service module that redirects the browser
 * cannot be used from a test, a retry, or any flow that wants to do something
 * before leaving the page.
 */
export function useConnectEbay() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (): Promise<EbayAuthorization> => {
      const { data } = await apiClient.post<EbayAuthorization>(
        "/integrations/ebay/connect",
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: integrationKeys.ebayStatus() });
    },
  });
}

export function useDisconnectEbay() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (): Promise<void> => {
      await apiClient.delete("/integrations/ebay/disconnect");
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: integrationKeys.ebayStatus() });
    },
  });
}
