"use client";

import { usePathname } from "next/navigation";

import { NavItem } from "@/components/navigation/nav-item";
import { NAV_FOOTER_ITEMS, NAV_SECTIONS, isNavItemActive } from "@/lib/navigation";
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
 * the identical list from the identical manifest. Section headings are hidden
 * when collapsed — there is no room for them — but the grouping survives as
 * spacing, and the list is still announced as one navigation landmark.
 *
 * Density is deliberate (UX-L2D-02): at 36px per item and modest section gaps
 * every section fits above the fold at 1440×900 with the pinned footer below;
 * shorter viewports scroll inside `NavScrollRegion`, which shows a fade where
 * items continue.
 */
export function SidebarNav({ collapsed, onNavigate, className }: SidebarNavProps) {
  const pathname = usePathname();
  const { data: counts } = useProductWorkspaceCounts();

  return (
    <nav
      aria-label="Main navigation"
      className={cn("flex flex-col gap-4 px-2 py-3", className)}
    >
      {NAV_SECTIONS.map((section) => (
        <div key={section.id} className="space-y-0.5">
          {!collapsed && section.label && (
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

interface SidebarFooterNavProps {
  collapsed?: boolean;
  onNavigate?: () => void;
}

/**
 * Items pinned beneath the scrolling list — Settings today.
 *
 * A separate landmark so a screen-reader user hears "Secondary navigation"
 * rather than a second, unexplained list; visually it sits above the collapse
 * control at any viewport height, which is the point of pinning it.
 */
export function SidebarFooterNav({ collapsed, onNavigate }: SidebarFooterNavProps) {
  const pathname = usePathname();

  return (
    <nav aria-label="Secondary navigation" className="px-2 py-2">
      {NAV_FOOTER_ITEMS.map((item) => (
        <NavItem
          key={item.href}
          item={item}
          active={isNavItemActive(item, pathname)}
          collapsed={collapsed}
          onNavigate={onNavigate}
        />
      ))}
    </nav>
  );
}
