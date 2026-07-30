"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps, ReactNode } from "react";

type ThemeProviderProps = ComponentProps<typeof NextThemesProvider> & {
  children: ReactNode;
};

/**
 * Theme provider for light and dark mode.
 *
 * `next-themes` writes the theme class onto <html> from an inline script that
 * runs before first paint. Doing this in a React effect instead would render
 * the light theme for one frame before switching — the "flash of wrong theme"
 * that is very obvious to anyone using dark mode.
 */
export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>;
}
