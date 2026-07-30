"use client";

import {
  BarChart3,
  LayoutDashboard,
  Package,
  ShoppingCart,
  Store,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentType } from "react";

import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/ui-store";

interface NavItem {
  href: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  /**
   * Whether the destination actually exists.
   *
   * Unbuilt destinations render as disabled items rather than links. Primary
   * navigation must never lead to a 404 — that reads as a broken product, not
   * as an unfinished one, and it is the difference between "this is coming" and
   * "this is broken".
   *
   * Keeping them visible is deliberate: the information architecture is part of
   * the product's story, and hiding it entirely would make the application look
   * emptier than it is. Set `ready: true` in the phase that builds the page.
   */
  ready: boolean;
}

/**
 * Navigation manifest.
 *
 * Data rather than markup, so the sidebar, a future command palette, and
 * breadcrumbs can all read the same source instead of each hardcoding routes
 * that then fall out of sync.
 */
const NAV_ITEMS: readonly NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, ready: true },
  { href: "/products", label: "Products", icon: Package, ready: false },
  { href: "/stores", label: "Stores", icon: Store, ready: false },
  { href: "/orders", label: "Orders", icon: ShoppingCart, ready: false },
  { href: "/analytics", label: "Analytics", icon: BarChart3, ready: false },
  { href: "/users", label: "Team", icon: Users, ready: false },
];

export function Sidebar() {
  const pathname = usePathname();
  const collapsed = useUIStore((state) => state.sidebarCollapsed);

  return (
    <aside
      className={cn(
        "hidden border-r bg-card transition-all duration-200 md:flex md:flex-col",
        collapsed ? "md:w-16" : "md:w-64",
      )}
    >
      <div className="flex h-16 items-center border-b px-4">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            DP
          </div>
          {!collapsed && <span className="truncate">DropPilot AI</span>}
        </Link>
      </div>

      <nav aria-label="Main navigation" className="flex-1 space-y-1 p-2">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;

          if (!item.ready) {
            return (
              <div
                key={item.href}
                // Not a link and not a button: there is nothing to activate.
                // `aria-disabled` announces the state without the element
                // being focusable and then doing nothing, which is worse than
                // not being focusable at all.
                aria-disabled="true"
                title={`${item.label} — coming soon`}
                className={cn(
                  "flex cursor-not-allowed items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground/60",
                  collapsed && "justify-center px-2",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed && (
                  <>
                    <span className="truncate">{item.label}</span>
                    <span className="ml-auto rounded-full border px-1.5 py-0.5 text-[10px] font-normal uppercase tracking-wide">
                      Soon
                    </span>
                  </>
                )}
                {/* The badge is hidden when collapsed, so the state still needs
                    a text equivalent for assistive technology. */}
                {collapsed && <span className="sr-only">Coming soon</span>}
              </div>
            );
          }

          // Prefix match so that /products/123 keeps Products highlighted.
          const isActive =
            pathname === item.href || pathname.startsWith(`${item.href}/`);

          return (
            <Link
              key={item.href}
              href={item.href}
              // Communicates the active page to assistive technology; colour
              // alone would not.
              aria-current={isActive ? "page" : undefined}
              title={collapsed ? item.label : undefined}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                collapsed && "justify-center px-2",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
