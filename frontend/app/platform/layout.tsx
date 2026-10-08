import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PlatformShell } from "@/components/platform/platform-shell";

export const metadata: Metadata = {
  title: "Platform",
  robots: { index: false, follow: false },
};

/**
 * Track E5d / D-018 / D-019: the platform-operator console. Outside the tenant
 * app (no `AuthProvider`, no tenant navigation). The API decides whether it
 * exists: every platform route answers 404 unless the operator's network is
 * allowed. The token lives in memory, so client-side navigation between
 * these pages keeps the session and a reload ends it.
 */
export default function PlatformLayout({ children }: { children: ReactNode }) {
  return <PlatformShell>{children}</PlatformShell>;
}
