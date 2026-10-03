"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { clearAccessToken, onTokenCleared } from "@/lib/auth/token-store";
import * as authApi from "@/services/auth";
import type {
  AuthenticatedIdentity,
  LoginPayload,
  RegisterPayload,
  RoleName,
} from "@/types/api";

/**
 * Session state for the whole application.
 *
 * Deliberately React context rather than Zustand or React Query:
 *
 * * Not Zustand — that store is scoped to *UI* state, and session identity is
 *   neither UI state nor something a stray import should be able to mutate.
 * * Not React Query — the session is not cache-shaped. It has no key to
 *   invalidate, must be resolved exactly once before the first render decision,
 *   and every consumer needs the same instance rather than a per-hook cache
 *   entry.
 */

type SessionStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: SessionStatus;
  identity: AuthenticatedIdentity | null;
  login: (payload: LoginPayload) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<void>;
  acceptInvitation: (payload: authApi.AcceptInvitationPayload) => Promise<void>;
  logout: () => Promise<void>;
  refreshIdentity: () => Promise<void>;
  hasRole: (role: RoleName) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [identity, setIdentity] = useState<AuthenticatedIdentity | null>(null);

  /**
   * Restore the session on first mount.
   *
   * The access token lives only in memory, so a reload always starts signed
   * out from JavaScript's point of view. The httpOnly refresh cookie is what
   * actually carries the session across reloads, and this exchanges it.
   *
   * `status` starts as "loading" and consumers must handle that state.
   * Rendering the signed-out UI while this is in flight would flash the login
   * page at an already-authenticated user on every refresh.
   */
  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const restored = await authApi.restoreSession();
      if (cancelled) return;

      if (restored) {
        setIdentity(restored.identity);
        setStatus("authenticated");
      } else {
        setStatus("unauthenticated");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * React to the token being cleared elsewhere.
   *
   * The API client clears it when a refresh fails mid-session — an expired or
   * revoked refresh token, or a session terminated by reuse detection. Without
   * this subscription the UI would keep rendering as though signed in while
   * every request failed.
   */
  useEffect(() => {
    return onTokenCleared(() => {
      // Same wipe as logout: a mid-session refresh failure must not leave the
      // previous tenant's catalogue/orders in memory for the next sign-in
      // (audit A-02).
      queryClient.clear();
      setIdentity(null);
      setStatus("unauthenticated");
    });
  }, [queryClient]);

  const login = useCallback(
    async (payload: LoginPayload) => {
      const response = await authApi.login(payload);
      // Drop any stale cache from a prior session on this tab before mounting
      // the new identity's queries.
      queryClient.clear();
      setIdentity(response.identity);
      setStatus("authenticated");
      router.replace("/dashboard");
    },
    [router, queryClient],
  );

  const register = useCallback(
    async (payload: RegisterPayload) => {
      const response = await authApi.register(payload);
      queryClient.clear();
      setIdentity(response.identity);
      setStatus("authenticated");
      router.replace("/dashboard");
    },
    [router, queryClient],
  );

  // Track E4: joining a workspace signs the new member in, like registering.
  const acceptInvitation = useCallback(
    async (payload: authApi.AcceptInvitationPayload) => {
      const response = await authApi.acceptInvitation(payload);
      queryClient.clear();
      setIdentity(response.identity);
      setStatus("authenticated");
      router.replace("/dashboard");
    },
    [router, queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      clearAccessToken();
      // React Query holds tenant data in the tab; `router.refresh()` only
      // resets Next RSC payloads — it does not clear the QueryClient (A-02).
      queryClient.clear();
      setIdentity(null);
      setStatus("unauthenticated");
      // `replace`, not `push`: the browser back button must not return to an
      // authenticated screen after signing out.
      router.replace("/login");
      router.refresh();
    }
  }, [router, queryClient]);

  const refreshIdentity = useCallback(async () => {
    try {
      setIdentity(await authApi.fetchIdentity());
    } catch {
      setIdentity(null);
      setStatus("unauthenticated");
    }
  }, []);

  const hasRole = useCallback(
    (role: RoleName) => identity?.roles.includes(role) ?? false,
    [identity],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      identity,
      login,
      register,
      acceptInvitation,
      logout,
      refreshIdentity,
      hasRole,
    }),
    [status, identity, login, register, acceptInvitation, logout, refreshIdentity, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    // Throwing beats returning a null-shaped default: a component rendered
    // outside the provider is a wiring bug, and a silent default would surface
    // as a mysteriously signed-out user instead.
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return context;
}
