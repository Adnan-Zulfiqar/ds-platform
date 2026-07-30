"use client";

import { PanelLeft } from "lucide-react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { useUIStore } from "@/stores/ui-store";

/**
 * Application top bar.
 *
 * Deliberately sparse in Phase 0. The user menu, tenant switcher, notifications
 * and global search all belong here, but each depends on a feature that does not
 * exist yet — rendering disabled placeholders for them would be misleading UI.
 */
export function TopNav() {
  const toggleSidebar = useUIStore((state) => state.toggleSidebar);

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b bg-background px-4">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleSidebar}
          aria-label="Toggle sidebar"
        >
          <PanelLeft className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex items-center gap-2">
        <ThemeToggle />
      </div>
    </header>
  );
}
