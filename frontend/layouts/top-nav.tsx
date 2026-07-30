"use client";

import { LogOut, PanelLeft, User as UserIcon } from "lucide-react";
import { useState } from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/providers/auth-provider";
import { useUIStore } from "@/stores/ui-store";

export function TopNav() {
  const toggleSidebar = useUIStore((state) => state.toggleSidebar);
  const { identity, logout } = useAuth();
  const [signingOut, setSigningOut] = useState(false);

  async function handleLogout() {
    // Guard against a double click firing two sign-out requests, the second of
    // which races the redirect.
    if (signingOut) return;
    setSigningOut(true);
    await logout();
  }

  const displayName =
    identity?.user.fullName ?? identity?.user.email ?? "Account";

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

        {identity && (
          <span className="hidden truncate text-sm text-muted-foreground sm:inline">
            {identity.tenant.name}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2">
        <ThemeToggle />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="Account menu">
              <UserIcon className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>

          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>
              <div className="flex flex-col gap-0.5">
                <span className="truncate text-sm font-medium">{displayName}</span>
                {identity && (
                  <>
                    <span className="truncate text-xs font-normal text-muted-foreground">
                      {identity.user.email}
                    </span>
                    <span className="text-xs font-normal capitalize text-muted-foreground">
                      {identity.roles.join(", ")}
                    </span>
                  </>
                )}
              </div>
            </DropdownMenuLabel>

            <DropdownMenuSeparator />

            <DropdownMenuItem onSelect={() => void handleLogout()} disabled={signingOut}>
              <LogOut className="mr-2 h-4 w-4" />
              {signingOut ? "Signing out..." : "Sign out"}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
