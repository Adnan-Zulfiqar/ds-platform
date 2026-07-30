"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api-client";

/**
 * React Query provider.
 *
 * The client is created inside `useState` rather than at module scope. A
 * module-level client is shared across every request on the server, which in a
 * multi-tenant application means one customer's cached data can be served to
 * another. Per-component-instance creation gives each request its own cache.
 */
export function QueryProvider({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Data is considered fresh for a minute. The default of 0 refetches
            // on every mount, which for a dashboard of independent widgets means
            // a burst of duplicate requests on every navigation.
            staleTime: 60_000,
            gcTime: 5 * 60_000,

            retry: (failureCount, error) => {
              // Never retry a client error — a 404 or a validation failure will
              // fail identically three more times, and retrying a 401 delays
              // the redirect to login.
              if (error instanceof ApiError && !error.isRetryable) return false;
              return failureCount < 2;
            },
            retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 30_000),

            // Refetching on every window focus is the single most common source
            // of surprise load on an API. Reconnect is kept because data really
            // can be stale after an outage.
            refetchOnWindowFocus: false,
            refetchOnReconnect: true,
          },
          mutations: {
            // Mutations are not idempotent by default, so retrying one risks
            // duplicating a customer action.
            retry: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
