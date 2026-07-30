import axios, {
  AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  clearAccessToken,
  getAccessToken,
  setAccessToken,
} from "@/lib/auth/token-store";
import { env } from "@/lib/env";
import type { ApiErrorResponse, AuthResponse } from "@/types/api";

/**
 * The single HTTP client for the application.
 *
 * Components never call `fetch` or `axios` directly. Centralising it here is
 * what makes cross-cutting behaviour — correlation ids, credential handling,
 * error normalisation, transparent token refresh — apply everywhere instead of
 * being reimplemented, and usually forgotten, at each call site.
 */

/**
 * A normalised API error.
 *
 * Every failure reaching application code is one of these, whether it
 * originated as a 4xx envelope, a network timeout, or a CORS rejection. Without
 * that normalisation each caller would have to handle three unrelated shapes.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number | null;
  readonly details: ApiErrorResponse["details"];
  readonly requestId: string | null;

  constructor(params: {
    code: string;
    message: string;
    status: number | null;
    details?: ApiErrorResponse["details"];
    requestId?: string | null;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.code = params.code;
    this.status = params.status;
    this.details = params.details ?? [];
    this.requestId = params.requestId ?? null;
  }

  /** True when retrying could plausibly succeed. */
  get isRetryable(): boolean {
    if (this.status === null) return true; // network-level failure
    return this.status >= 500 || this.status === 429;
  }

  /** True when the user must authenticate. */
  get isAuthError(): boolean {
    return this.status === 401;
  }

  /** Field-level messages, keyed by field name, for form error display. */
  get fieldErrors(): Record<string, string> {
    const result: Record<string, string> = {};
    for (const detail of this.details) {
      if (detail.field) result[detail.field] = detail.message;
    }
    return result;
  }
}

function generateRequestId(): string {
  // crypto.randomUUID is unavailable on insecure origins and in older runtimes,
  // so fall back rather than throwing — a correlation id is useful, not
  // load-bearing.
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: `${env.NEXT_PUBLIC_API_URL}/api/v1`,
  timeout: env.NEXT_PUBLIC_API_TIMEOUT_MS,
  headers: { "Content-Type": "application/json" },
  // Required for the refresh cookie to be sent. The cookie is httpOnly, so this
  // is the only way it reaches the server.
  withCredentials: true,
});

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  config.headers.set("X-Request-ID", generateRequestId());

  const token = getAccessToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

// ---------------------------------------------------------------------------
// Transparent token refresh
// ---------------------------------------------------------------------------

/**
 * The in-flight refresh, if any.
 *
 * **Single-flight is essential, not an optimisation.** A dashboard typically
 * fires several requests at once; if the access token has expired they all
 * receive 401 simultaneously. Without this guard each would trigger its own
 * refresh, and because refresh tokens *rotate*, the first would consume the
 * token and the rest would present an already-consumed one — which the server
 * correctly treats as theft and responds to by terminating every session.
 *
 * In other words, omitting this does not merely waste requests: it logs the
 * user out.
 */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      // A bare axios call, not `apiClient`: routing it through the instance
      // would re-enter this interceptor on failure and recurse.
      const { data } = await axios.post<AuthResponse>(
        `${env.NEXT_PUBLIC_API_URL}/api/v1/auth/refresh`,
        {},
        { withCredentials: true, timeout: env.NEXT_PUBLIC_API_TIMEOUT_MS },
      );
      setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
      return true;
    } catch {
      // The refresh token is expired, revoked, or absent. The session is over.
      clearAccessToken();
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

/** Endpoints that must never trigger a refresh attempt. */
const NO_REFRESH_PATHS = ["/auth/login", "/auth/register", "/auth/refresh", "/auth/logout"];

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorResponse>) => {
    const config = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;

    if (error.response) {
      const status = error.response.status;
      const body = error.response.data;
      const isAuthEndpoint = NO_REFRESH_PATHS.some((path) => config?.url?.includes(path));

      // One refresh attempt, then one retry. `_retried` prevents a loop when
      // the retried request also returns 401.
      if (status === 401 && config && !config._retried && !isAuthEndpoint) {
        config._retried = true;
        if (await refreshAccessToken()) {
          return apiClient(config);
        }
      }

      return Promise.reject(
        new ApiError({
          code: body?.code ?? "http_error",
          message: body?.message ?? `Request failed with status ${status}.`,
          status,
          details: body?.details,
          requestId: body?.requestId ?? error.response.headers["x-request-id"] ?? null,
        }),
      );
    }

    // No response: timeout, DNS failure, offline, or a CORS rejection. These
    // are indistinguishable from the browser for security reasons, so the
    // message stays deliberately vague rather than guessing wrongly.
    const isTimeout = error.code === "ECONNABORTED";
    return Promise.reject(
      new ApiError({
        code: isTimeout ? "timeout" : "network_error",
        message: isTimeout
          ? "The request timed out. Please try again."
          : "Could not reach the server. Check your connection and try again.",
        status: null,
      }),
    );
  },
);
