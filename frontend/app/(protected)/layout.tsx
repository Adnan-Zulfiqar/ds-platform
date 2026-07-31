import type { ReactNode } from "react";

import { AuthGuard } from "@/components/auth-guard";
import { AppShell } from "@/layouts/app-shell";

/**
 * Layout for authenticated areas of the application.
 *
 * `(protected)` is a route group: the parentheses mean the segment shapes the
 * layout tree without appearing in the URL, so this wraps `/dashboard` and
 * `/products` while those paths stay clean.
 *
 * The group name describes intent; `AuthGuard` provides client-side
 * enforcement, and the API provides the actual security. Placing the guard here
 * rather than on each page means a route added under this group is protected by
 * default — the safe thing happens without anyone remembering to do it.
 *
 * Phase 2 moved the chrome into `AppShell`, leaving this file to compose the
 * two concerns. The authentication behaviour is unchanged.
 */
export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
