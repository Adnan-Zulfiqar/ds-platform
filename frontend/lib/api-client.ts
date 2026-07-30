import axios, {
  AxiosError,
  type AxiosInstance,
  type InternalAxiosRequestConfig,
} from "axios";

import { env } from "@/lib/env";
import type { ApiErrorResponse } from "@/types/api";

/**
 * The single HTTP client for the application.
 *
 * Components never call `fetch` or `axios` directly. Centralising it here is
 * what makes cross-cutting behaviour — correlation ids, credential handling,
 * error normalisation — apply everywhere instead of being reimplemented, and
 * usually forgotten, at each call site.
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
  // Send cookies cross-origin. The auth phase issues httpOnly cookies, which
  // are immune to token theft via XSS in a way that localStorage is not.
  withCredentials: true,
});

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  // Generated client-side so a trace can be followed from the browser through
  // the API and into any background job it triggers.
  config.headers.set("X-Request-ID", generateRequestId());
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorResponse>) => {
    // A response with the standard envelope: use the server's own code.
    if (error.response) {
      const body = error.response.data;
      return Promise.reject(
        new ApiError({
          code: body?.code ?? "http_error",
          message: body?.message ?? `Request failed with status ${error.response.status}.`,
          status: error.response.status,
          details: body?.details,
          requestId:
            body?.requestId ?? error.response.headers["x-request-id"] ?? null,
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
