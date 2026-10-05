import { useQuery } from "@tanstack/react-query";
import axios from "axios";

import { apiClient } from "@/lib/api-client";
import { setAccessToken } from "@/lib/auth/token-store";
import { env } from "@/lib/env";
import { legalAcceptance } from "@/lib/legal";
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
  // Acceptance travels with every signup, as the value the person actually
  // gave. The backend refuses a false or mismatched one, so a caller that
  // forgets gets a clear failure rather than a silent bypass.
  const { acceptedLegal, ...account } = payload;
  const { data } = await apiClient.post<AuthResponse>("/auth/register", {
    ...account,
    ...legalAcceptance(acceptedLegal),
  });
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
 * Revoke every session of the signed-in user, on every device. Unlike
 * `logout`, a failure is surfaced: the user asked for a security action and
 * must know if it did not happen. The caller follows it with `logout()` to
 * clear this tab's own state.
 */
export async function logoutAll(): Promise<string> {
  const { data } = await apiClient.post<{ message: string }>("/auth/logout-all", {});
  return data.message;
}

/** Ask the server to (re)send the email verification link. */
export async function requestEmailVerification(): Promise<string> {
  const { data } = await apiClient.post<{ message: string }>("/auth/verify-email/request", {});
  return data.message;
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

export type GoogleIntent = "login" | "signup";

/**
 * Ask the server for a one-time nonce, bound to one operation.
 *
 * Google embeds it in the credential it signs, which is what lets the backend
 * tell a fresh attempt from a replayed one. The intent is recorded server-side,
 * so a nonce obtained for signing in cannot later be presented to signup or
 * link.
 */
export async function requestGoogleNonce(intent: GoogleIntent): Promise<GoogleNonce> {
  const { data } = await apiClient.post<GoogleNonce>("/auth/google/nonce", { intent });
  return data;
}

/**
 * Sign in with an already-linked Google account.
 *
 * The credential is passed through untouched — nothing decodes it here, because
 * what the browser thinks it says is irrelevant and only the backend's
 * signature check counts.
 *
 * **Creates nothing.** If the Google account is not linked, this fails; it does
 * not quietly register a workspace, which the previous combined endpoint did.
 */
export async function loginWithGoogle(payload: {
  credential: string;
  nonce: string;
}): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>("/auth/google/login", payload);
  setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
  return data;
}

/**
 * Create an account with Google, carrying the same acceptance a password
 * signup needs. The backend enforces it; this only transports it.
 */
export async function signUpWithGoogle(payload: {
  credential: string;
  nonce: string;
  companyName?: string;
  acceptedLegal: boolean;
}): Promise<AuthResponse> {
  const { acceptedLegal, ...rest } = payload;
  const { data } = await apiClient.post<AuthResponse>("/auth/google/signup", {
    ...rest,
    ...legalAcceptance(acceptedLegal),
  });
  setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
  return data;
}

/** Link Google to the signed-in account. Requires the account password. */
export async function linkGoogle(payload: {
  credential: string;
  nonce: string;
  password: string;
}): Promise<void> {
  await apiClient.post("/auth/google/link", payload);
}

/** A link nonce, bound server-side to the signed-in user. */
export async function requestGoogleLinkNonce(): Promise<GoogleNonce> {
  const { data } = await apiClient.post<GoogleNonce>("/auth/google/link/nonce", {});
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

/** Track E4: what a team invitation link is for, before accepting it. */
export interface InvitationPreview {
  email: string;
  role: string;
  workspaceName: string;
  expiresAt: string;
}

export async function previewInvitation(token: string): Promise<InvitationPreview> {
  const { data } = await apiClient.post<InvitationPreview>("/auth/invitations/preview", {
    token,
  });
  return data;
}

export interface AcceptInvitationPayload {
  token: string;
  password: string;
  firstName?: string;
  lastName?: string;
  acceptedLegal: boolean;
}

export async function acceptInvitation(payload: AcceptInvitationPayload): Promise<AuthResponse> {
  const { acceptedLegal, ...rest } = payload;
  const { data } = await apiClient.post<AuthResponse>("/auth/invitations/accept", {
    ...rest,
    ...legalAcceptance(acceptedLegal),
  });
  setAccessToken(data.tokens.accessToken, data.tokens.expiresIn);
  return data;
}

/** The preview is keyed by the token; it is never retried, because a 404
 * here is an answer ("this link is dead"), not a transient failure. */
export function useInvitationPreview(token: string | null) {
  return useQuery({
    queryKey: [...authKeys.all, "invitation", token],
    queryFn: () => previewInvitation(token as string),
    enabled: Boolean(token),
    retry: false,
  });
}
