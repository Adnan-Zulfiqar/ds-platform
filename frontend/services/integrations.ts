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
  EbayListingDefaults,
  EbayListingDefaultsPayload,
  EbayListingSetup,
  EbayLocation,
  EbayLocationPayload,
  EbayCategorySuggestion,
  EbayProductDetails,
  EbayProductDetailsPayload,
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
  ebayListingSetup: (marketplaceId: string) =>
    [...integrationKeys.ebay(), "listing-setup", marketplaceId] as const,
  ebayProductDetails: (productId: string, marketplaceId: string) =>
    [...integrationKeys.ebay(), "product-details", productId, marketplaceId] as const,
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
      // The listing defaults go with the connection (cascade), so every
      // cached eBay read is stale, not only the status.
      void queryClient.invalidateQueries({ queryKey: integrationKeys.ebay() });
    },
  });
}

/**
 * EBAY-C2 listing setup: the seller's live policies and locations plus the
 * saved defaults, for one marketplace. Read live by the server on every call,
 * so this is fetched only when the panel is open (`enabled`).
 */
export function useEbayListingSetup(
  marketplaceId: string,
  enabled: boolean,
): UseQueryResult<EbayListingSetup> {
  return useQuery({
    queryKey: integrationKeys.ebayListingSetup(marketplaceId),
    queryFn: async () => {
      const { data } = await apiClient.get<EbayListingSetup>(
        "/integrations/ebay/listing-setup",
        { params: { marketplaceId } },
      );
      return data;
    },
    enabled,
    // Each read costs five eBay calls; a merchant switching tabs should not
    // repeat them. Saving invalidates explicitly.
    staleTime: 60_000,
  });
}

export function useSaveEbayListingDefaults() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: EbayListingDefaultsPayload): Promise<EbayListingDefaults> => {
      const { data } = await apiClient.put<EbayListingDefaults>(
        "/integrations/ebay/listing-defaults",
        payload,
      );
      return data;
    },
    onSuccess: (saved, payload) => {
      // The saved choice becomes the cached default immediately, so the form
      // never shows the pre-save values next to "Saved". The policy and
      // location lists themselves did not change, so no eBay refetch.
      queryClient.setQueryData<EbayListingSetup>(
        integrationKeys.ebayListingSetup(payload.marketplaceId),
        (current) => (current ? { ...current, defaults: saved } : current),
      );
    },
  });
}

export function useCreateEbayLocation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: EbayLocationPayload): Promise<EbayLocation> => {
      const { data } = await apiClient.post<EbayLocation>("/integrations/ebay/locations", payload);
      return data;
    },
    onSuccess: () => {
      // Locations are per seller, not per marketplace: every setup read is stale.
      void queryClient.invalidateQueries({
        queryKey: [...integrationKeys.ebay(), "listing-setup"],
      });
    },
  });
}

/**
 * EBAY-C3: a product's eBay category and item specifics. Each read asks eBay
 * for the category's current aspects, so it is fetched only while the eBay
 * details section is on screen.
 */
export function useEbayProductDetails(
  productId: string,
  marketplaceId: string,
  enabled: boolean,
): UseQueryResult<EbayProductDetails> {
  return useQuery({
    queryKey: integrationKeys.ebayProductDetails(productId, marketplaceId),
    queryFn: async () => {
      const { data } = await apiClient.get<EbayProductDetails>(
        `/integrations/ebay/products/${productId}/details`,
        { params: { marketplaceId } },
      );
      return data;
    },
    enabled,
    staleTime: 60_000,
  });
}

export async function fetchEbayCategorySuggestions(
  productId: string,
  marketplaceId: string,
  query?: string,
): Promise<EbayCategorySuggestion[]> {
  const { data } = await apiClient.get<EbayCategorySuggestion[]>(
    `/integrations/ebay/products/${productId}/category-suggestions`,
    { params: { marketplaceId, ...(query ? { q: query } : {}) } },
  );
  return data;
}

export function useSaveEbayProductDetails(productId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: EbayProductDetailsPayload): Promise<EbayProductDetails> => {
      const { data } = await apiClient.put<EbayProductDetails>(
        `/integrations/ebay/products/${productId}/details`,
        payload,
      );
      return data;
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(
        integrationKeys.ebayProductDetails(productId, saved.marketplaceId),
        saved,
      );
    },
  });
}

/** EBAY-C4: send a product's current price and stock to its eBay listings now. */
export async function sendEbayPriceQuantity(productId: string): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    `/integrations/ebay/products/${productId}/sync-price-quantity`,
  );
  return data;
}

/** EBAY-C5: import eBay orders changed in the last `days` days. */
export async function importEbayOrders(days = 7): Promise<{ fetched: number; created: number; updated: number }> {
  const { data } = await apiClient.post<{ fetched: number; created: number; updated: number }>(
    "/integrations/ebay/orders/import",
    undefined,
    { params: { days } },
  );
  return data;
}

/** EBAY-C5: tell eBay an order shipped. Repeating a tracking number is a no-op. */
export async function shipEbayOrder(
  orderId: string,
  payload: { carrierCode: string; trackingNumber: string },
): Promise<{ id: string }> {
  const { data } = await apiClient.post<{ id: string }>(
    `/integrations/ebay/orders/${orderId}/shipments`,
    payload,
  );
  return data;
}
