import axios from "axios";

import { apiClient } from "@/lib/api-client";
import { setAccessToken } from "@/lib/auth/token-store";
import { env } from "@/lib/env";
import type {
  AuthenticatedIdentity,
  AuthResponse,
  LoginPayload,
  RegisterPayload,
} from "@/types/api";

/**
 * Authentication API calls.
 *
 * Each function stores the returned access token as a side effect, so callers
 * never handle the raw credential. That keeps token handling in one place
 * rather than spread across every component that can sign a user in.
 */

export const authKeys = {
  all: ["auth"] as const,
  session: () => [...authKeys.all, "session"] as const,
};

export async function register(payload: RegisterPayload): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>("/auth/register", payload);
  setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
  return data;
}

export async function login(payload: LoginPayload): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>("/auth/login", payload);
  setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
  return data;
}

export async function fetchIdentity(): Promise<AuthenticatedIdentity> {
  const { data } = await apiClient.get<AuthenticatedIdentity>("/auth/me");
  return data;
}

export async function logout(): Promise<void> {
  // Errors are swallowed. Sign-out must always appear to succeed from the
  // user's side; the client clears its own state regardless, and the refresh
  // token expires on its own even if the revocation call never arrived.
  try {
    await apiClient.post("/auth/logout", {});
  } catch {
    // Intentionally ignored — see above.
  }
}

/**
 * Exchange the httpOnly refresh cookie for a new access token.
 *
 * Called once when the app boots, because the access token lives only in memory
 * and is lost on reload. A bare axios call rather than `apiClient` so that a
 * failure here — the ordinary case for a signed-out visitor — does not trip the
 * client's own refresh interceptor.
 *
 * Returns `null` when there is no usable session, which is not an error: it is
 * simply how an anonymous visitor looks.
 */
export async function restoreSession(): Promise<AuthResponse | null> {
  try {
    const { data } = await axios.post<AuthResponse>(
      `${env.NEXT_PUBLIC_API_URL}/api/v1/auth/refresh`,
      {},
      { withCredentials: true, timeout: env.NEXT_PUBLIC_API_TIMEOUT_MS },
    );
    setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
    return data;
  } catch {
    return null;
  }
}
