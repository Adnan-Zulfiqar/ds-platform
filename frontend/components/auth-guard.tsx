"use client";

import { Loader2 } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/providers/auth-provider";

/**
 * Client-side route guard for authenticated areas.
 *
 * This is where "protected routes redirect unauthenticated users" actually
 * happens — middleware cannot see the httpOnly, path-scoped session cookie, as
 * explained in `middleware.ts`.
 *
 * Three states, and each must be handled distinctly:
 *
 * * **loading** — the session is still being restored from the refresh cookie.
 *   Redirecting now would throw an authenticated user out on every page
 *   refresh, which is the single most common bug in this pattern.
 * * **unauthenticated** — redirect to sign-in, preserving the destination.
 * * **authenticated** — render.
 *
 * Bypassing this reveals an empty shell. The data behind it is protected by the
 * API, which verifies a signed token on every request.
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (status !== "unauthenticated") return;

    // Preserve where the user was heading so they land there after signing in
    // rather than always on the dashboard.
    const next = encodeURIComponent(pathname);
    // `replace`, not `push`: the protected page must not remain in history,
    // where the back button would return to it.
    router.replace(`/login?next=${next}`);
  }, [status, router, pathname]);

  if (status === "loading") {
    return (
      <div
        className="flex min-h-screen items-center justify-center"
        role="status"
        aria-live="polite"
      >
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        <span className="sr-only">Checking your session</span>
      </div>
    );
  }

  if (status === "unauthenticated") {
    // Render nothing while the redirect runs. Returning `children` here would
    // flash protected chrome, and its data requests would fail with 401 noise.
    return null;
  }

  return <>{children}</>;
}
