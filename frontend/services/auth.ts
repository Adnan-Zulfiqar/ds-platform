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

/**
 * Google sign-in and password reset (AUTH-G1).
 *
 * Every one of these goes through this module rather than being called from a
 * component, so the endpoint, the payload shape and the response type stay
 * defined in one place.
 */

export interface GoogleNonce {
  nonce: string;
  expiresInSeconds: number;
}

/**
 * Ask the server for a one-time nonce.
 *
 * Google embeds it in the credential it signs, which is what lets the backend
 * tell a fresh sign-in from a replayed one.
 */
export async function requestGoogleNonce(): Promise<GoogleNonce> {
  const { data } = await apiClient.post<GoogleNonce>("/auth/google/nonce", {});
  return data;
}

/**
 * Exchange a Google credential for a DropPilot session.
 *
 * The credential is passed through untouched. Nothing decodes it here: what the
 * browser thinks it says is irrelevant, and only the backend's verification of
 * the signature counts.
 */
export async function signInWithGoogle(payload: {
  credential: string;
  nonce?: string;
}): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>("/auth/google", payload);
  setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
  return data;
}

export interface PasswordResetChallenge {
  challengeId: string;
  expiresInSeconds: number;
  message: string;
}

/**
 * Request a reset code.
 *
 * The response is identical whether or not the address has an account, so
 * nothing the caller does with it may imply otherwise.
 */
export async function requestPasswordReset(email: string): Promise<PasswordResetChallenge> {
  const { data } = await apiClient.post<PasswordResetChallenge>(
    "/auth/password-reset/request",
    { email },
  );
  return data;
}

export interface PasswordResetTicket {
  resetTicket: string;
  expiresInSeconds: number;
}

export async function verifyPasswordResetCode(payload: {
  challengeId: string;
  code: string;
}): Promise<PasswordResetTicket> {
  const { data } = await apiClient.post<PasswordResetTicket>(
    "/auth/password-reset/verify",
    payload,
  );
  return data;
}

export async function completePasswordReset(payload: {
  resetTicket: string;
  newPassword: string;
}): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    "/auth/password-reset/complete",
    payload,
  );
  return data;
}
