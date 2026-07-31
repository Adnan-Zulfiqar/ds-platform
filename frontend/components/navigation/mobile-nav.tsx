"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { SidebarNav } from "@/components/navigation/sidebar-nav";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useIsDesktop } from "@/hooks/use-media-query";
import { useUIStore } from "@/stores/ui-store";

/**
 * Mobile navigation drawer.
 *
 * Renders the same `SidebarNav` as the desktop rail, so the two cannot diverge.
 *
 * Two closing behaviours that are easy to omit and obvious when missing:
 *
 * * **Closes on navigation.** A drawer left open over the page the user just
 *   navigated to hides the thing they asked for.
 * * **Closes when the viewport grows past the breakpoint.** Otherwise rotating
 *   a tablet leaves an orphaned overlay above a sidebar that is now visible
 *   anyway, with no visible way to dismiss it.
 *
 * Open state lives in `ui-store` but is deliberately not persisted — restoring
 * a drawer as open on the next visit would be wrong.
 */
export function MobileNav() {
  const open = useUIStore((state) => state.mobileSidebarOpen);
  const setOpen = useUIStore((state) => state.setMobileSidebarOpen);
  const pathname = usePathname();
  const isDesktop = useIsDesktop();

  useEffect(() => {
    if (isDesktop && open) setOpen(false);
  }, [isDesktop, open, setOpen]);

  // Belt and braces alongside the `onNavigate` callback: this also covers
  // navigation triggered from outside the drawer while it is open.
  useEffect(() => {
    setOpen(false);
  }, [pathname, setOpen]);

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        onClick={() => setOpen(true)}
        aria-label="Open navigation menu"
      >
        <Menu className="h-4 w-4" />
      </Button>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetHeader className="h-16 justify-center border-b px-4 text-left">
            <SheetTitle className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">
                DP
              </span>
              DropPilot AI
            </SheetTitle>
            {/* Radix warns without a description, and a screen reader announces
                it when the panel opens. */}
            <SheetDescription className="sr-only">
              Application navigation
            </SheetDescription>
          </SheetHeader>

          <div className="h-[calc(100%-4rem)] overflow-y-auto">
            <SidebarNav onNavigate={() => setOpen(false)} />
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
