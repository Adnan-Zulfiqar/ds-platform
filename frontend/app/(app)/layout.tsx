import type { ReactNode } from "react";

import { AuthProvider } from "@/providers/auth-provider";

/**
 * Boundary between the application and the handful of genuinely public pages.
 *
 * `AuthProvider` mounts exactly once, here, for every route that has a session:
 * the signed-out forms in `(auth)` and the authenticated area in `(protected)`.
 * Routes outside this group — `/privacy`, `/unauthorized`, `/`, and the root
 * error and not-found boundaries — render without it and perform no
 * authentication bootstrap at all.
 *
 * **Why one shared layout rather than the provider in each group.** Sibling
 * route groups do not share a layout instance, so mounting `AuthProvider` in
 * `(auth)` and again in `(protected)` remounts it when a user crosses from the
 * sign-in form to the dashboard. That remount fires a second session restore,
 * and `router.replace` navigates away while it is still in flight. Refresh
 * tokens rotate on use with reuse detection, so the server issued a new token
 * whose `Set-Cookie` the aborted response never delivered — leaving the browser
 * holding a spent token and the next request answered with 401. A common
 * ancestor keeps one provider instance across that navigation, which is what
 * the root layout used to provide before the public policy page needed to be
 * outside it.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}
