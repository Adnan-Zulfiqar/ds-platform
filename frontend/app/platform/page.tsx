import type { Metadata } from "next";

import { PlatformConsole } from "@/components/platform/platform-console";

export const metadata: Metadata = { title: "Platform", robots: { index: false, follow: false } };

/**
 * Track E5d: the platform-operator console. Outside the tenant app (no
 * `AuthProvider`, no tenant navigation). The API decides whether it exists:
 * every platform route answers 404 unless the operator's network is allowed.
 */
export default function PlatformPage() {
  return <PlatformConsole />;
}
