"use client";

import { CircleHelp, Search } from "lucide-react";
import { usePathname } from "next/navigation";

import { MobileNav } from "@/components/navigation/mobile-nav";
import { NotificationMenu } from "@/components/navigation/notification-menu";
import { UserMenu } from "@/components/navigation/user-menu";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { findNavItem } from "@/lib/navigation";

/**
 * Application top bar.
 *
 * Layout is deliberately asymmetric by viewport. On mobile the left slot holds
 * the drawer trigger; on desktop it holds the current page name, which is
 * redundant while the sidebar shows the highlighted route but useful once the
 * sidebar is collapsed to icons.
 *
 * Search and help render as controls without behaviour, which is a deliberate
 * limit of this phase: global search needs an index of products and orders that
 * do not exist, and the help centre has no content. They are marked
 * `disabled` — visible so the layout is final, honest about not working. A
 * button that silently does nothing is worse than one that says it cannot.
 */
export function TopNav() {
  const pathname = usePathname();
  const currentPage = findNavItem(pathname);

  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-2 border-b bg-background px-3 sm:px-4">
      <div className="flex min-w-0 items-center gap-2">
        <MobileNav />
        <span className="hidden truncate text-sm font-medium md:inline">
          {currentPage?.label ?? "DropPilot AI"}
        </span>
      </div>

      <div className="flex shrink-0 items-center gap-0.5 sm:gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            {/* A span wrapper: a disabled button fires no pointer events, so
                Radix would never see the hover that opens the tooltip. */}
            <span>
              <Button
                variant="ghost"
                size="icon"
                disabled
                aria-label="Search — coming soon"
              >
                <Search className="h-4 w-4" />
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>Global search — coming soon</TooltipContent>
        </Tooltip>

        <NotificationMenu />

        <Tooltip>
          <TooltipTrigger asChild>
            <span>
              <Button
                variant="ghost"
                size="icon"
                disabled
                aria-label="Help — coming soon"
                className="hidden sm:inline-flex"
              >
                <CircleHelp className="h-4 w-4" />
              </Button>
            </span>
          </TooltipTrigger>
          <TooltipContent>Help centre — coming soon</TooltipContent>
        </Tooltip>

        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
