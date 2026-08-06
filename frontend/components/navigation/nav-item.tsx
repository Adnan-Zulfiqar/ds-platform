"use client";

import Link from "next/link";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { NavItem as NavItemData } from "@/lib/navigation";
import { cn } from "@/lib/utils";

interface NavItemProps {
  item: NavItemData;
  active: boolean;
  /** Icons only. Labels move into tooltips. */
  collapsed?: boolean;
  /** Called after navigation, so the mobile drawer can close itself. */
  onNavigate?: () => void;
  /** Live workspace count when `item.badgeKey` is set. */
  badgeCount?: number;
}

/**
 * One navigation entry, in either of its two forms.
 *
 * Shared by the desktop sidebar and the mobile drawer so the two cannot drift —
 * the highlight rule, the disabled treatment, and the accessible labelling are
 * defined once.
 *
 * A `coming-soon` item renders as a plain `div`, not a disabled link or button.
 * A disabled interactive element is still reachable by keyboard and then does
 * nothing when activated, which is more confusing than an element that was
 * never interactive. `aria-disabled` communicates the state without the dead
 * stop.
 */
export function NavItem({
  item,
  active,
  collapsed,
  onNavigate,
  badgeCount,
}: NavItemProps) {
  const Icon = item.icon;
  const unavailable = item.status === "coming-soon";
  const showCount =
    typeof badgeCount === "number" && Number.isFinite(badgeCount);

  const shared = cn(
    "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
    collapsed && "justify-center px-2",
  );

  const content = unavailable ? (
    <div
      aria-disabled="true"
      className={cn(shared, "cursor-not-allowed text-muted-foreground/60")}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      {!collapsed && (
        <>
          <span className="truncate">{item.label}</span>
          <span className="ml-auto shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] font-normal uppercase tracking-wide">
            Soon
          </span>
        </>
      )}
      {/* The badge is hidden when collapsed, so the state still needs a text
          equivalent for assistive technology. */}
      {collapsed && <span className="sr-only">{item.label} — coming soon</span>}
    </div>
  ) : (
    <Link
      href={item.href}
      onClick={onNavigate}
      // Communicates the current page to assistive technology. Colour alone
      // would not.
      aria-current={active ? "page" : undefined}
      className={cn(
        shared,
        "transition-colors",
        active
          ? "bg-primary text-primary-foreground"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      {!collapsed && <span className="truncate">{item.label}</span>}
      {!collapsed && showCount ? (
        <span
          className={cn(
            "ml-auto shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-medium tabular-nums",
            active
              ? "bg-primary-foreground/15 text-primary-foreground"
              : "bg-muted text-muted-foreground",
          )}
          data-testid={`nav-badge-${item.badgeKey}`}
        >
          {badgeCount}
        </span>
      ) : null}
      {collapsed && (
        <span className="sr-only">
          {item.label}
          {showCount ? ` (${badgeCount})` : ""}
        </span>
      )}
    </Link>
  );

  // Tooltips only when collapsed: with labels visible they would repeat what is
  // already on screen. Note the tooltip is an enhancement, never the only
  // label — it does not appear on touch devices, which is why the sr-only text
  // above exists regardless.
  if (!collapsed) return content;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{content}</TooltipTrigger>
      <TooltipContent side="right">
        <p className="font-medium">{item.label}</p>
        {unavailable && <p className="text-muted-foreground">Coming soon</p>}
      </TooltipContent>
    </Tooltip>
  );
}
