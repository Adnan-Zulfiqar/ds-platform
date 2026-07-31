import type { ListQuery } from "@/types/api";

/**
 * Store connection data access — **structure only**.
 *
 * `GET /api/v1/stores` is a registered router with no routes.
 *
 * One constraint worth recording before this is implemented: store connections
 * hold third-party credentials, which must be encrypted at rest with a key held
 * outside the database, and **must never be returned to the client** — not even
 * masked. The read types defined in the implementing phase should expose
 * connection *status*, never secrets.
 */

export const storeKeys = {
  all: ["stores"] as const,
  lists: () => [...storeKeys.all, "list"] as const,
  list: (query: ListQuery) => [...storeKeys.lists(), query] as const,
  details: () => [...storeKeys.all, "detail"] as const,
  detail: (id: string) => [...storeKeys.details(), id] as const,
};

/** Sales channels the platform targets. */
export type StoreProvider =
  | "shopify"
  | "woocommerce"
  | "ebay"
  | "etsy"
  | "tiktok-shop"
  | "aliexpress";

export type StoreConnectionStatus = "connected" | "disconnected" | "error" | "syncing";
