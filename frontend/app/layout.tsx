import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
import { headers } from "next/headers";
import type { ReactNode } from "react";

import { AppProviders } from "@/providers";

import "./globals.css";

// Self-hosted at build time by next/font: no runtime request to Google, which
// removes a third-party dependency from the critical render path and avoids the
// layout shift that a late-loading webfont causes.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: {
    default: "DropPilot AI",
    template: "%s | DropPilot AI",
  },
  description: "Multi-tenant dropshipping automation platform.",
  // The application is behind authentication and has no public content worth
  // indexing.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // maximumScale is deliberately not set. Blocking pinch-zoom is an
  // accessibility failure for anyone who needs to magnify the interface.
};

/**
 * Every page renders per request, and that is a deliberate cost.
 *
 * The CSP carries a nonce minted in `middleware.ts` for each response, and a
 * page prerendered at build time has its script tags written long before that
 * nonce exists — they would arrive without one and the browser would refuse to
 * run them. Reading a request header here opts the whole tree into dynamic
 * rendering so the nonce is applied.
 *
 * What that costs: nothing is served from the build-time static cache, so each
 * page render does the work rather than replaying stored HTML. It is a small
 * bill for this application — every route below `/` is an authenticated,
 * user-specific dashboard that was already dynamic, and the three public pages
 * (`/login`, `/register`, `/privacy`) are static markup with no data fetching.
 * Downstream HTTP caching is unaffected: protected routes already send
 * `no-store`, and public ones are unchanged.
 */
export default async function RootLayout({ children }: { children: ReactNode }) {
  // `middleware.ts` puts the nonce here. Read rather than regenerated: a second
  // value would not match the one in the policy this response carries.
  const nonce = (await headers()).get("x-nonce") ?? undefined;

  return (
    // suppressHydrationWarning is required by next-themes: it writes the theme
    // class onto <html> before React hydrates, so server and client markup
    // legitimately differ on this one element.
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} font-sans antialiased`}>
        {/* Lets keyboard users bypass the navigation on every page. Visually
            hidden until focused. */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
        >
          Skip to main content
        </a>
        <AppProviders nonce={nonce}>{children}</AppProviders>
      </body>
    </html>
  );
}
