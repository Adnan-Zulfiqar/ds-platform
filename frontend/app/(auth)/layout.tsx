import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Shell for unauthenticated pages: sign in, register, password reset.
 *
 * A route group, so `/login` stays `/login` rather than `/auth/login`. It has no
 * sidebar or top bar — a signed-out visitor has nothing to navigate to, and
 * showing chrome that leads nowhere is worse than showing none.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-muted/30">
      <main
        id="main-content"
        className="flex flex-1 items-center justify-center p-4 sm:p-8"
      >
        <div className="w-full max-w-md">{children}</div>
      </main>

      {/* The privacy policy is linked from every signed-out page, not only from
          the marketing site: eBay requires a reachable policy URL, and someone
          deciding whether to create an account should be able to read it
          before they do. */}
      <footer className="pb-6 text-center text-sm text-muted-foreground">
        <p>DropPilot AI</p>
        <p className="mt-1">
          <Link
            className="underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            href="/privacy"
          >
            Privacy Policy
          </Link>
        </p>
      </footer>
    </div>
  );
}
