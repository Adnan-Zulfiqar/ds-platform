import type { ReactNode } from "react";

import { AuthGuard } from "@/components/auth-guard";
import { Sidebar } from "@/layouts/sidebar";
import { TopNav } from "@/layouts/top-nav";

/**
 * Shell for authenticated areas of the application.
 *
 * `(protected)` is a route group: the parentheses mean the segment shapes the
 * layout tree without appearing in the URL, so this layout wraps `/dashboard`
 * and `/products` while those paths stay clean.
 *
 * The group name describes intent; `AuthGuard` provides the enforcement, and
 * the API provides the actual security. Placing the guard here rather than on
 * each page means a new route added under this group is protected by default —
 * the safe thing happens without anyone remembering to do it.
 */
export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopNav />
          {/* Scrolling is confined to the main region so the sidebar and top bar
              stay fixed. `id` is the skip-link target from the root layout. */}
          <main id="main-content" className="flex-1 overflow-y-auto">
            {children}
          </main>
        </div>
      </div>
    </AuthGuard>
  );
}
