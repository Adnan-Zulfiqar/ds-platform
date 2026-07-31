import type { ListQuery } from "@/types/api";

/**
 * Product data access — **structure only**.
 *
 * `GET /api/v1/products` is a registered router with no routes. Nothing here
 * calls the API.
 *
 * The query keys follow the same hierarchy as `services/users.ts`, which is the
 * working reference for this pattern. Keeping the shape identical across
 * services means cache invalidation behaves predictably rather than each module
 * inventing its own convention.
 */

export const productKeys = {
  all: ["products"] as const,
  lists: () => [...productKeys.all, "list"] as const,
  list: (query: ListQuery) => [...productKeys.lists(), query] as const,
  details: () => [...productKeys.all, "detail"] as const,
  detail: (id: string) => [...productKeys.details(), id] as const,
};
