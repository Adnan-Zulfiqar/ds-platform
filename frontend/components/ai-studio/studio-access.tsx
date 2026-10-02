"use client";

import type { ReactNode } from "react";

import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/providers/auth-provider";

/** Owners and admins can use AI Studio; every pipeline route is `RequireAdmin`. */
export function useCanUseStudio(): { ready: boolean; allowed: boolean } {
  const { status, hasRole } = useAuth();
  return {
    ready: status === "authenticated",
    allowed: hasRole("owner") || hasRole("admin"),
  };
}

/**
 * Renders `children` only for owners and admins (plan §17).
 *
 * Presentation, not the security boundary: the API answers 403 to anyone
 * else. Its job is to send no pipeline request at all once the role is known
 * to be insufficient, and to say why instead of showing a broken page.
 */
export function StudioAccess({ children }: { children: ReactNode }) {
  const { ready, allowed } = useCanUseStudio();

  if (!ready) {
    return <Skeleton className="h-64 w-full" data-testid="ai-studio-loading" />;
  }
  if (!allowed) {
    return (
      <div data-testid="ai-studio-permission">
        <ErrorState
          title="AI Studio"
          description="AI Studio is available to owners and admins."
        />
      </div>
    );
  }
  return <>{children}</>;
}
