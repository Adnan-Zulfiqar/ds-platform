"use client";

import type { ReactNode } from "react";

import { QueryProvider } from "@/providers/query-provider";
import { ThemeProvider } from "@/providers/theme-provider";

/**
 * Composed application providers.
 *
 * A single component so that the root layout has one provider element rather
 * than a growing pyramid of nesting, and so that provider *order* is decided
 * here — deliberately — instead of accidentally at the call site.
 *
 * Theme sits outermost: it only writes a class onto <html> and has no
 * dependency on data fetching, whereas a future data-driven provider may well
 * need the theme.
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
      <QueryProvider>{children}</QueryProvider>
    </ThemeProvider>
  );
}
