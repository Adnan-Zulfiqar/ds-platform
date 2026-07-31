"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";

import { SidebarNav } from "@/components/navigation/sidebar-nav";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/ui-store";

/**
 * Desktop sidebar.
 *
 * Hidden below the `md` breakpoint, where `MobileNav` takes over. The split is
 * a CSS media query rather than a JavaScript check so the correct one is
 * present in the server-rendered HTML — a JS-driven choice would render the
 * wrong navigation until hydration.
 *
 * The collapsed preference is persisted in `ui-store`, so it survives a reload.
 * Width animates rather than snapping, which makes the change legible; the
 * duration is short enough not to feel like an effect, and
 * `prefers-reduced-motion` disables it globally via `globals.css`.
 */
export function Sidebar() {
  const collapsed = useUIStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUIStore((state) => state.toggleSidebar);

  return (
    <aside
      className={cn(
        "hidden shrink-0 flex-col border-r bg-card transition-[width] duration-200 md:flex",
        collapsed ? "md:w-16" : "md:w-64",
      )}
    >
      <div
        className={cn(
          "flex h-16 shrink-0 items-center border-b",
          collapsed ? "justify-center px-2" : "px-4",
        )}
      >
        <Link
          href="/dashboard"
          className="flex items-center gap-2 font-semibold"
          aria-label="DropPilot AI — go to dashboard"
        >
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">
            DP
          </div>
          {!collapsed && <span className="truncate">DropPilot AI</span>}
        </Link>
      </div>

      {/* The nav scrolls independently, so a long list never pushes the collapse
          control off the bottom of the viewport. */}
      <div className="flex-1 overflow-y-auto">
        <SidebarNav collapsed={collapsed} />
      </div>

      <div className={cn("shrink-0 border-t p-2", collapsed && "flex justify-center")}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size={collapsed ? "icon" : "sm"}
              onClick={toggleSidebar}
              // The label states the resulting action, not the current state —
              // "Expand sidebar" tells the user what pressing it does.
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={cn(!collapsed && "w-full justify-start gap-3")}
            >
              {collapsed ? (
                <PanelLeftOpen className="h-4 w-4" />
              ) : (
                <>
                  <PanelLeftClose className="h-4 w-4" />
                  <span>Collapse</span>
                </>
              )}
            </Button>
          </TooltipTrigger>
          {collapsed && <TooltipContent side="right">Expand sidebar</TooltipContent>}
        </Tooltip>
      </div>
    </aside>
  );
}
