"use client";

import { usePathname } from "next/navigation";

import { NavItem } from "@/components/navigation/nav-item";
import { NAV_SECTIONS, isNavItemActive } from "@/lib/navigation";
import { cn } from "@/lib/utils";
import { useProductWorkspaceCounts } from "@/services/products";

interface SidebarNavProps {
  collapsed?: boolean;
  onNavigate?: () => void;
  className?: string;
}

/**
 * The navigation list itself, without any surrounding chrome.
 *
 * Separated from `Sidebar` so the desktop rail and the mobile drawer render
 * identical navigation from one component. Duplicating the list into a
 * `MobileNav` is the usual approach and the usual source of a route that exists
 * on one and not the other.
 */
export function SidebarNav({ collapsed, onNavigate, className }: SidebarNavProps) {
  const pathname = usePathname();
  const { data: counts } = useProductWorkspaceCounts();

  return (
    <nav
      aria-label="Main navigation"
      className={cn("flex flex-col gap-6 px-2 py-4", className)}
    >
      {NAV_SECTIONS.map((section) => (
        <div key={section.id} className="space-y-1">
          {/* Section headings are hidden when collapsed — there is no room for
              them — but the grouping survives as spacing, and the list is still
              announced as one navigation landmark. */}
          {!collapsed && (
            <h2 className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">
              {section.label}
            </h2>
          )}

          {section.items.map((item) => (
            <NavItem
              key={item.href}
              item={item}
              active={isNavItemActive(item, pathname)}
              collapsed={collapsed}
              onNavigate={onNavigate}
              badgeCount={
                item.badgeKey && counts
                  ? counts[item.badgeKey]
                  : undefined
              }
            />
          ))}
        </div>
      ))}
    </nav>
  );
}
