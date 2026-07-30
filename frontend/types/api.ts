/**
 * Types mirroring the backend API contract.
 *
 * These are maintained by hand in Phase 0 because the surface is two endpoints.
 * That does not scale: hand-written types drift from the server silently, and
 * the drift only surfaces as a runtime bug. Before the API grows, generate
 * these from the OpenAPI document the backend already publishes at
 * `/openapi.json` (openapi-typescript) and delete this file.
 */

/** Error envelope returned by every failing endpoint. */
export interface ApiErrorDetail {
  field: string | null;
  message: string;
  type: string | null;
}

export interface ApiErrorResponse {
  /** Stable, machine-readable. Branch on this, never on `message`. */
  code: string;
  /** Human-readable and subject to change without notice. */
  message: string;
  details: ApiErrorDetail[];
  /** Correlates with the server log line. Show it in support surfaces. */
  requestId: string | null;
}

/** Pagination metadata accompanying every list response. */
export interface PageMeta {
  page: number;
  size: number;
  totalItems: number;
  totalPages: number;
  hasNext: boolean;
  hasPrevious: boolean;
}

/** A page of results. Generic over the item type. */
export interface Page<T> {
  items: T[];
  meta: PageMeta;
}

export type UserRole = "owner" | "admin" | "member" | "viewer";

export interface User {
  id: string;
  tenantId: string;
  email: string;
  fullName: string | null;
  role: UserRole;
  isActive: boolean;
  emailVerifiedAt: string | null;
  lastLoginAt: string | null;
  createdAt: string;
  updatedAt: string;
}

/** Query parameters accepted by every list endpoint. */
export interface ListQuery {
  page?: number;
  size?: number;
  sortBy?: string;
  sortDir?: "asc" | "desc";
  q?: string;
}

export type HealthStatus = "healthy" | "degraded" | "unhealthy";

export interface ComponentHealth {
  name: string;
  status: HealthStatus;
  detail: string | null;
}

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  environment: string;
  components: ComponentHealth[];
}
