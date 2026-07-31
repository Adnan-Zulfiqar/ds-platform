import {
  BarChart3,
  Bot,
  Download,
  LayoutDashboard,
  Package,
  Settings,
  ShoppingCart,
  Store,
  Truck,
  Users,
} from "lucide-react";
import type { ComponentType } from "react";

/**
 * The single source of truth for application navigation.
 *
 * Data, not markup. The desktop sidebar, the mobile drawer, and any future
 * command palette or breadcrumb trail all read from here, so a route added in
 * one place cannot be missing from another. Phase 1 had the manifest inside the
 * sidebar component, which meant it was reachable by exactly one consumer.
 *
 * **`status` is the mechanism that keeps navigation honest.** Primary
 * navigation must never lead to a 404 — that reads as a broken product rather
 * than an unfinished one. Destinations that do not exist yet are declared
 * `coming-soon` and render as non-interactive items. The phase that builds a
 * page flips its status and adds the route; nothing else changes.
 */

export type NavStatus = "ready" | "coming-soon";

export interface NavItem {
  /** Route path. Present even for `coming-soon` items, as the intended target. */
  href: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  status: NavStatus;
  /** Shown in tooltips and on placeholder pages. */
  description: string;
}

export interface NavSection {
  id: string;
  /** Section heading. Hidden when the sidebar is collapsed. */
  label: string;
  items: readonly NavItem[];
}

export const NAV_SECTIONS: readonly NavSection[] = [
  {
    id: "main",
    label: "Main",
    items: [
      {
        href: "/dashboard",
        label: "Dashboard",
        icon: LayoutDashboard,
        status: "ready",
        description: "Overview of your store performance.",
      },
    ],
  },
  {
    id: "product-management",
    label: "Product Management",
    items: [
      {
        href: "/products",
        label: "Products",
        icon: Package,
        status: "ready",
        description: "Manage your product catalogue across every channel.",
      },
      {
        href: "/products/import",
        label: "Import Product",
        icon: Download,
        status: "coming-soon",
        description: "Import products from AliExpress and other suppliers.",
      },
      {
        href: "/suppliers",
        label: "Suppliers",
        icon: Truck,
        status: "coming-soon",
        description: "Manage supplier relationships and sourcing rules.",
      },
    ],
  },
  {
    id: "sales",
    label: "Sales",
    items: [
      {
        href: "/orders",
        label: "Orders",
        icon: ShoppingCart,
        status: "ready",
        description: "Track and fulfil customer orders.",
      },
      {
        href: "/customers",
        label: "Customers",
        icon: Users,
        status: "coming-soon",
        description: "View customer profiles and purchase history.",
      },
    ],
  },
  {
    id: "stores",
    label: "Stores",
    items: [
      {
        href: "/stores",
        label: "Connected Stores",
        icon: Store,
        status: "ready",
        description: "Connect and manage your sales channels.",
      },
    ],
  },
  {
    id: "analytics",
    label: "Analytics",
    items: [
      {
        href: "/analytics",
        label: "Analytics",
        icon: BarChart3,
        status: "ready",
        description: "Revenue, profit, and performance reporting.",
      },
    ],
  },
  {
    id: "system",
    label: "System",
    items: [
      {
        href: "/automation",
        label: "Automation",
        icon: Bot,
        status: "coming-soon",
        description: "Rules for pricing, inventory, and fulfilment.",
      },
      {
        href: "/settings",
        label: "Settings",
        icon: Settings,
        status: "ready",
        description: "Workspace, team, and billing configuration.",
      },
    ],
  },
] as const;

/** Every item across all sections, flattened. */
export const ALL_NAV_ITEMS: readonly NavItem[] = NAV_SECTIONS.flatMap(
  (section) => section.items,
);

/**
 * Resolve the navigation item matching a pathname.
 *
 * Matches the **longest** href rather than the first, so `/products/import`
 * resolves to the import item and not to `/products`. A first-match scan would
 * label every product subpage "Products".
 */
export function findNavItem(pathname: string): NavItem | undefined {
  return ALL_NAV_ITEMS.filter(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  ).sort((a, b) => b.href.length - a.href.length)[0];
}

/** Whether a nav item should render as the active route. */
export function isNavItemActive(item: NavItem, pathname: string): boolean {
  // Exact match only for `/dashboard`; a prefix match would keep it highlighted
  // on every route, since every path starts with `/`.
  if (item.href === "/dashboard") return pathname === item.href;
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}
