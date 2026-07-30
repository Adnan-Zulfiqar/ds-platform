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

      <footer className="pb-6 text-center text-sm text-muted-foreground">
        <p>DropPilot AI</p>
      </footer>
    </div>
  );
}
