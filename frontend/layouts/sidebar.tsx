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
}

/**
 * Navigation manifest.
 *
 * Data rather than markup, so the sidebar, a future command palette, and
 * breadcrumbs can all read the same source instead of each hardcoding routes
 * that then fall out of sync.
 *
 * Destinations beyond the dashboard are listed because the routes are part of
 * the Phase 0 information architecture; the pages behind them arrive with their
 * own phases.
 */
const NAV_ITEMS: readonly NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/products", label: "Products", icon: Package },
  { href: "/stores", label: "Stores", icon: Store },
  { href: "/orders", label: "Orders", icon: ShoppingCart },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/users", label: "Team", icon: Users },
];

export function Sidebar() {
  const pathname = usePathname();
  const collapsed = useUIStore((state) => state.sidebarCollapsed);

  return (
    <aside
      // <nav> inside <aside> with a label: a screen reader user can jump
      // straight to navigation, and the label distinguishes it from other
      // navigation landmarks on the page.
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
          // Prefix match so that /products/123 keeps Products highlighted.
          const isActive =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;

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
