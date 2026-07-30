import type { ReactNode } from "react";

import { Sidebar } from "@/layouts/sidebar";
import { TopNav } from "@/layouts/top-nav";

/**
 * Shell for authenticated areas of the application.
 *
 * `(protected)` is a route group: the parentheses mean the segment shapes the
 * layout tree without appearing in the URL, so this layout wraps `/dashboard`
 * and `/products` while those paths stay clean.
 *
 * **The group name describes intent, not enforcement.** Access control is
 * applied in `middleware.ts`, which runs before any of this renders. A route
 * group is a file-system convention and provides no security whatsoever — the
 * separation exists so that when auth lands there is exactly one place to gate,
 * and no public page is sitting inside the protected tree by accident.
 */
export default function ProtectedLayout({ children }: { children: ReactNode }) {
  return (
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
  );
}
