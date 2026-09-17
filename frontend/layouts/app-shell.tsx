"use client";

import type { ReactNode } from "react";

import { Sidebar } from "@/components/navigation/sidebar";
import { TopNav } from "@/components/navigation/top-nav";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * The authenticated application shell.
 *
 * ```
 * ┌──────────┬────────────────────────────┐
 * │ Sidebar  │ Top navigation             │
 * │          ├────────────────────────────┤
 * │          │ Main content (scrolls)     │
 * └──────────┴────────────────────────────┘
 * ```
 *
 * Below `md` the sidebar is hidden and `MobileNav` supplies a drawer from the
 * top bar.
 *
 * **Scrolling is confined to `main`**, not the document. The sidebar and top
 * bar stay put without `position: fixed`, which avoids the usual problems that
 * brings — content sliding underneath, and mobile browsers mismeasuring the
 * viewport as their address bar collapses.
 *
 * `TooltipProvider` sits here rather than in the root providers because
 * tooltips are only used inside the application shell. Mounting it around the
 * sign-in pages would add a provider those routes never consult.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-screen overflow-hidden bg-background">
        <Sidebar />

        <div className="flex min-w-0 flex-1 flex-col">
          <TopNav />

          {/* `id` is the skip-link target from the root layout; `tabIndex={-1}`
              lets the link actually move focus here in every browser, not only
              the ones that move the sequential-focus start on a fragment jump.
              `min-w-0` stops a wide child — a table, a chart — forcing the
              whole shell to scroll horizontally instead of scrolling within
              its own box. */}
          <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 overflow-y-auto outline-none">
            {children}
          </main>
        </div>
      </div>
    </TooltipProvider>
  );
}
