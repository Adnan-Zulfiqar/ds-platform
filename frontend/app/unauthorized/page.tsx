import { ShieldX } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { Button } from "@/components/ui/button";

export const metadata: Metadata = { title: "Access denied" };

/**
 * Shown when a user is authenticated but lacks the required permission.
 *
 * **Distinct from the sign-in redirect**, which handles "we do not know who you
 * are". This handles "we know exactly who you are, and the answer is no".
 * Sending someone to a login form when they are already signed in is a
 * confusing dead end — they re-enter correct credentials and arrive back at the
 * same wall.
 *
 * Deliberately outside the `(protected)` group: rendering it inside the shell
 * would require passing the guard it exists to report on.
 *
 * The API is the real enforcement point and returns 403 with
 * `permission_denied`. This page is where the client sends the user in
 * response.
 */
export default function UnauthorizedPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-4 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10">
        <ShieldX className="h-6 w-6 text-destructive" aria-hidden="true" />
      </div>

      <h1 className="text-2xl font-semibold">Access denied</h1>
      <p className="max-w-md text-muted-foreground">
        Your account does not have permission to view this page. If you believe
        this is a mistake, ask a workspace owner or administrator to review your
        role.
      </p>

      <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
        <Button asChild>
          <Link href="/dashboard">Back to dashboard</Link>
        </Button>
      </div>
    </div>
  );
}
