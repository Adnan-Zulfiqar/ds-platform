"use client";

import type { ReactNode } from "react";

import { AuthProvider } from "@/providers/auth-provider";
import { QueryProvider } from "@/providers/query-provider";
import { ThemeProvider } from "@/providers/theme-provider";

/**
 * Composed application providers.
 *
 * A single component so that the root layout has one provider element rather
 * than a growing pyramid of nesting, and so that provider *order* is decided
 * here — deliberately — instead of accidentally at the call site.
 *
 * Order, outermost first:
 *
 * 1. `ThemeProvider` — only writes a class onto <html>; depends on nothing.
 * 2. `QueryProvider` — must wrap `AuthProvider`, because signing out calls
 *    `queryClient.clear()` (and `router.refresh()` for RSC) and needs the
 *    client in tree.
 * 3. `AuthProvider` — innermost of the three, so its consumers can use both
 *    theming and data fetching.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      // Suppress the CSS transition that would otherwise animate every colour
      // on the page during a theme switch, which looks like a rendering glitch.
      disableTransitionOnChange
    >
      <QueryProvider>
        <AuthProvider>{children}</AuthProvider>
      </QueryProvider>
    </ThemeProvider>
  );
}
