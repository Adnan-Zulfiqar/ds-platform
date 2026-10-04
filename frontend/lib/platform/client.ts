import axios, { type AxiosError, type AxiosInstance } from "axios";

import { toApiError } from "@/lib/api-client";
import { env } from "@/lib/env";
import type { ApiErrorResponse } from "@/types/api";

/**
 * The platform-operator API client (Track E5d, D-015).
 *
 * Deliberately separate from `apiClient`:
 *
 * * it never sends the tenant access token, and the tenant client never
 *   sends this one, because the two identities must not meet;
 * * no refresh: platform sessions are short and are not refreshable, so a
 *   401 means "sign in again";
 * * no cookies (`withCredentials: false`): nothing about a tenant session
 *   rides along.
 *
 * The token lives in memory only. A reload ends the operator session, which
 * is the intended behaviour for a 30-minute, TOTP-guarded credential.
 */

let platformToken: string | null = null;
const listeners = new Set<() => void>();

export function getPlatformToken(): string | null {
  return platformToken;
}

export function setPlatformToken(token: string | null): void {
  platformToken = token;
  listeners.forEach((listener) => listener());
}

export function subscribePlatformToken(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export const platformClient: AxiosInstance = axios.create({
  baseURL: `${env.NEXT_PUBLIC_API_URL}/api/v1/platform`,
  timeout: env.NEXT_PUBLIC_API_TIMEOUT_MS,
  headers: { "Content-Type": "application/json" },
  withCredentials: false,
});

platformClient.interceptors.request.use((config) => {
  if (platformToken) {
    config.headers.set("Authorization", `Bearer ${platformToken}`);
  }
  return config;
});

platformClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiErrorResponse>) => {
    if (error.response?.status === 401) {
      // Expired or refused: the operator signs in again.
      setPlatformToken(null);
    }
    return Promise.reject(toApiError(error));
  },
);
