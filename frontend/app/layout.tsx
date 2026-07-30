import type { Metadata, Viewport } from "next";
import { Inter } from "next/font/google";
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

export default function RootLayout({ children }: { children: ReactNode }) {
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
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
