"use client";

import { ChevronRight } from "lucide-react";
import { usePathname } from "next/navigation";

import { MobileNav } from "@/components/navigation/mobile-nav";
import { NotificationMenu } from "@/components/navigation/notification-menu";
import { UserMenu } from "@/components/navigation/user-menu";
import { ThemeToggle } from "@/components/theme-toggle";
import { findNavItem, findNavSection } from "@/lib/navigation";

/**
 * Application top bar.
 *
 * Layout is deliberately asymmetric by viewport. On mobile the left slot holds
 * the drawer trigger; on desktop it holds a two-level trail — section and
 * page — which is redundant while the sidebar shows the highlighted route but
 * useful once the sidebar is collapsed to icons, and the only place a nested
 * route such as `/settings/integrations` says where it lives.
 *
 * The earlier Search and Help controls are gone (UX-L2D-02). They rendered as
 * permanently disabled buttons — honest about not working, but two dead
 * controls in the most prominent bar of the product read as broken rather
 * than unfinished. They return with the features, not before.
 */
export function TopNav() {
  const pathname = usePathname();
  const currentPage = findNavItem(pathname);
  const section = currentPage ? findNavSection(currentPage) : undefined;
  const sectionLabel = section?.label;

  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-2 border-b bg-background px-3 sm:px-4">
      <div className="flex min-w-0 items-center gap-2">
        <MobileNav />
        <nav
          aria-label="Breadcrumb"
          className="hidden min-w-0 items-center gap-1 text-sm md:flex"
        >
          {sectionLabel && (
            <>
              <span className="truncate text-muted-foreground">{sectionLabel}</span>
              <ChevronRight
                className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60"
                aria-hidden="true"
              />
            </>
          )}
          <span aria-current="page" className="truncate font-medium">
            {currentPage?.label ?? "DropPilot AI"}
          </span>
        </nav>
      </div>

      <div className="flex shrink-0 items-center gap-0.5 sm:gap-1">
        <NotificationMenu />
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
