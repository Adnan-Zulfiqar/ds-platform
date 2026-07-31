"use client";

import { LogOut, Settings, User as UserIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Avatar, AvatarFallback, getInitials } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/providers/auth-provider";

/**
 * Account menu.
 *
 * Shows who the user is, which tenant they are acting in, and what role they
 * hold. The tenant matters more than it might seem: a consultant with accounts
 * in several workspaces needs to know which one they are about to change before
 * they change it.
 *
 * Sign-out goes through the existing `useAuth().logout`, which revokes the
 * refresh token server-side, clears the in-memory access token, and redirects.
 * Nothing about the Phase 1 authentication flow is reimplemented here.
 */
export function UserMenu() {
  const { identity, logout, status } = useAuth();
  const [signingOut, setSigningOut] = useState(false);

  async function handleLogout() {
    // Guards against a double click firing two sign-out requests, the second
    // racing the redirect.
    if (signingOut) return;
    setSigningOut(true);
    await logout();
  }

  if (status === "loading") {
    return <Skeleton className="h-9 w-9 rounded-full" />;
  }

  const user = identity?.user;
  const displayName = user?.fullName ?? user?.email ?? "Account";

  // A user who has not set a name falls back to their email address as the
  // display name — in which case showing the email again underneath is pure
  // duplication. Common on freshly registered accounts, since registration
  // treats first and last name as optional.
  const showEmailLine = Boolean(user?.email) && displayName !== user?.email;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="rounded-full"
          aria-label="Account menu"
        >
          <Avatar>
            <AvatarFallback>
              {getInitials(user?.fullName, user?.email)}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col gap-1">
            <p className="truncate text-sm font-medium">{displayName}</p>
            {showEmailLine && (
              <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
            )}

            {identity && (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                {identity.roles.map((role) => (
                  <Badge key={role} variant="secondary" className="capitalize">
                    {role}
                  </Badge>
                ))}
                <span className="truncate text-xs text-muted-foreground">
                  {identity.tenant.name}
                </span>
              </div>
            )}
          </div>
        </DropdownMenuLabel>

        <DropdownMenuSeparator />

        <DropdownMenuItem asChild>
          <Link href="/settings/profile">
            <UserIcon className="mr-2 h-4 w-4" />
            Profile
          </Link>
        </DropdownMenuItem>

        <DropdownMenuItem asChild>
          <Link href="/settings">
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </Link>
        </DropdownMenuItem>

        <DropdownMenuSeparator />

        <DropdownMenuItem
          onSelect={(event) => {
            // Prevent the menu closing before the async work starts, so the
            // "Signing out..." label is actually visible.
            event.preventDefault();
            void handleLogout();
          }}
          disabled={signingOut}
        >
          <LogOut className="mr-2 h-4 w-4" />
          {signingOut ? "Signing out..." : "Log out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
