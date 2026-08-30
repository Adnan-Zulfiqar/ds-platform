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
 * Order, outermost first:
 *
 * 1. `ThemeProvider` — only writes a class onto <html>; depends on nothing.
 * 2. `QueryProvider` — must wrap `AuthProvider`, because signing out calls
 *    `queryClient.clear()` (and `router.refresh()` for RSC) and needs the
 *    client in tree.
 *
 * **`AuthProvider` is deliberately not here.** It mounts a session-restore
 * effect that issues `POST /auth/refresh` on first render, so mounting it at the
 * root made every route — including the public privacy policy — perform an
 * authentication bootstrap before rendering. A page that anyone may read with no
 * account must not call an authenticated endpoint at all.
 *
 * It now mounts in the two layouts whose subtrees actually consume it:
 * `app/(auth)/layout.tsx` and `app/(protected)/layout.tsx`. Both sit inside this
 * component, so both still get theming and a `QueryClient`. Routes outside those
 * groups — `/privacy`, `/unauthorized`, `/`, and the root error and not-found
 * boundaries — consume no session and now mount none.
 */
export function AppProviders({
  children,
  nonce,
}: {
  children: ReactNode;
  /**
   * The CSP nonce for this response, read from the request in the root layout.
   *
   * `next-themes` writes its own inline `<script>` to set the theme class
   * before React hydrates — that is the whole point of it, and it is why the
   * page does not flash white on a dark-mode reload. Next.js stamps its own
   * script tags with the nonce but has no way to reach into a library's, so
   * without this the browser refuses to run it and every reload flashes.
   */
  nonce?: string;
}) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      nonce={nonce}
      // Suppress the CSS transition that would otherwise animate every colour
      // on the page during a theme switch, which looks like a rendering glitch.
      disableTransitionOnChange
    >
      <QueryProvider>{children}</QueryProvider>
    </ThemeProvider>
  );
}
