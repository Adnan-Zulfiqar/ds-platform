import {
  BarChart3,
  Bot,
  FileEdit,
  History,
  LayoutDashboard,
  Package,
  Plug,
  Settings,
  ShoppingCart,
  SlidersHorizontal,
  Sparkles,
  Store,
  Tags,
  Truck,
  Users,
  Warehouse,
} from "lucide-react";
import type { ComponentType } from "react";

import type { RoleName } from "@/types/api";

/**
 * The single source of truth for application navigation.
 *
 * Data, not markup. The desktop sidebar, the mobile drawer, the top-bar
 * breadcrumb and any future command palette all read from here, so a route
 * added in one place cannot be missing from another. Phase 1 had the manifest
 * inside the sidebar component, which meant it was reachable by exactly one
 * consumer.
 *
 * **Sections are merchant jobs, not subsystems** (UX-L2D-02). "Catalogue"
 * reads top-to-bottom as the product loop — Drafts → Products → Import
 * history; "Channels" is the merchant's word for the Shopify/AliExpress/eBay
 * connections and points at the page that can actually connect and repair
 * one; "Automation" gathers the four rule-driven pages that previously sat in
 * three different sections. Every href here is a real route.
 *
 * **`status` is the mechanism that keeps navigation honest.** Primary
 * navigation must never lead to a 404 — that reads as a broken product rather
 * than an unfinished one. A `coming-soon` item renders as a non-interactive
 * entry. Since UX-L2D-02 the primary manifest carries no such items: planned
 * destinations live in `PLANNED_NAV_ITEMS` so their intent is recorded without
 * the sidebar advertising what does not exist yet. The phase that builds a
 * page moves its entry into a section and flips the status.
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
  /** When set, the sidebar shows a live count from workspace-counts. */
  badgeKey?: "drafts" | "products";
  /**
   * Match the pathname exactly rather than by prefix. Needed where an item's
   * href is itself a prefix of other items' hrefs (`/settings` next to
   * `/settings/integrations`), so the parent does not light up on every child.
   */
  exact?: boolean;
  /**
   * Roles that see this item. Omitted means everyone. Presentation only:
   * the API enforces the same boundary, so hiding an item is about not
   * offering a page whose every action would be refused.
   */
  roles?: readonly RoleName[];
}

export interface NavSection {
  id: string;
  /**
   * Section heading. Hidden when the sidebar is collapsed. Omitted for a
   * section whose single item already says everything — a "Home" heading
   * above a "Home" link is noise.
   */
  label?: string;
  items: readonly NavItem[];
}

export const NAV_SECTIONS: readonly NavSection[] = [
  {
    id: "home",
    items: [
      {
        href: "/dashboard",
        label: "Home",
        icon: LayoutDashboard,
        status: "ready",
        description: "What needs your attention and what to do next.",
        exact: true,
      },
    ],
  },
  {
    id: "catalogue",
    label: "Catalogue",
    items: [
      {
        href: "/drafts",
        label: "Drafts",
        icon: FileEdit,
        status: "ready",
        description: "Imported products awaiting review and Publish to Store.",
        badgeKey: "drafts",
      },
      {
        href: "/products",
        label: "Products",
        icon: Package,
        status: "ready",
        description:
          "Products successfully published to at least one connected store.",
        badgeKey: "products",
      },
      {
        href: "/imports/history",
        label: "Import history",
        icon: History,
        status: "ready",
        description: "Import jobs, failures, and retries.",
      },
      {
        href: "/ai-studio",
        label: "AI Studio",
        icon: Sparkles,
        status: "ready",
        description: "Generate AI proposals and review them before approving or publishing.",
        roles: ["owner", "admin"],
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
        href: "/shipments",
        label: "Shipments",
        icon: Truck,
        status: "ready",
        description: "Carrier tracking and delivery status.",
      },
    ],
  },
  {
    id: "channels",
    label: "Channels",
    items: [
      {
        href: "/settings/integrations",
        label: "Integrations",
        icon: Plug,
        status: "ready",
        description: "Connect and repair suppliers and sales channels.",
      },
      {
        href: "/stores",
        label: "Stores",
        icon: Store,
        status: "ready",
        description: "Store records: last sync and activity for every store the workspace has known.",
      },
    ],
  },
  {
    id: "automation",
    label: "Automation",
    items: [
      {
        href: "/automation",
        label: "Rules",
        icon: Bot,
        status: "ready",
        description: "Rules for pricing, inventory, and fulfilment.",
      },
      {
        href: "/pricing",
        label: "Pricing",
        icon: Tags,
        status: "ready",
        description: "Markup rules and sell-price previews.",
      },
      {
        href: "/inventory",
        label: "Inventory",
        icon: Warehouse,
        status: "ready",
        description: "Stock levels and supplier inventory sync.",
      },
      {
        href: "/settings/global-rules",
        label: "Global rules",
        icon: SlidersHorizontal,
        status: "ready",
        description: "Pricing and shipping rules applied across your catalogue.",
      },
    ],
  },
  {
    id: "reports",
    label: "Reports",
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
] as const;

/**
 * Items pinned below the scrolling sections, so they stay reachable at any
 * viewport height. Settings holds workspace, team and billing configuration;
 * it must never disappear below the fold.
 */
export const NAV_FOOTER_ITEMS: readonly NavItem[] = [
  {
    href: "/settings",
    label: "Settings",
    icon: Settings,
    status: "ready",
    description: "Workspace, team, and billing configuration.",
    exact: true,
  },
] as const;

/**
 * Destinations that are planned but not built. Not rendered in the sidebar —
 * a placeholder in primary navigation advertises a capability the product does
 * not have. Kept here so the intent and copy survive until their phase; the
 * `/customers` route still serves its `ComingSoon` page by URL.
 */
export const PLANNED_NAV_ITEMS: readonly NavItem[] = [
  {
    href: "/suppliers",
    label: "Suppliers",
    icon: Truck,
    status: "coming-soon",
    description: "Manage supplier relationships and sourcing rules.",
  },
  {
    href: "/customers",
    label: "Customers",
    icon: Users,
    status: "coming-soon",
    description: "View customer profiles and purchase history.",
  },
] as const;

/** Every rendered item — sections and footer — flattened. */
export const ALL_NAV_ITEMS: readonly NavItem[] = [
  ...NAV_SECTIONS.flatMap((section) => section.items),
  ...NAV_FOOTER_ITEMS,
];

function matchesPrefix(item: NavItem, pathname: string): boolean {
  return pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/**
 * Resolve the navigation item matching a pathname.
 *
 * Matches the **longest** href rather than the first, so a nested route such as
 * `/settings/integrations` resolves to Integrations rather than Settings, and
 * `/drafts/123` to Drafts. A first-match scan would label nested pages
 * incorrectly when order matters.
 */
export function findNavItem(pathname: string): NavItem | undefined {
  return ALL_NAV_ITEMS.filter((item) => matchesPrefix(item, pathname)).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];
}

/** The section an item belongs to; `undefined` for footer items. */
export function findNavSection(item: NavItem): NavSection | undefined {
  return NAV_SECTIONS.find((section) => section.items.includes(item));
}

/**
 * Whether a nav item should render as the active route.
 *
 * `exact` items match only themselves: `/dashboard` would otherwise stay
 * highlighted on every route (every path starts with `/`), and `/settings`
 * would light up alongside its own children.
 */
export function isNavItemActive(item: NavItem, pathname: string): boolean {
  if (item.exact) return pathname === item.href;
  return matchesPrefix(item, pathname);
}
