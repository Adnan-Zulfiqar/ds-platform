import Link from "next/link";

import {
  COMPANY_NUMBER,
  LEGAL_ENTITY,
  REGISTERED_OFFICE,
} from "@/components/legal/company-disclosure";
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
        {/* Both legal documents, and the statutory company disclosures that
            regulation 25 of the 2015 Trading Disclosures Regulations and
            regulation 6 of the E-Commerce Regulations require to be available.
            This footer is on every sign-in and registration page, which is
            where somebody decides whether to contract with us. */}
        <p className="mt-1 flex flex-wrap justify-center gap-4">
          <Link
            className="underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            href="/terms"
          >
            Terms of Service
          </Link>
          <Link
            className="underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            href="/privacy"
          >
            Privacy Policy
          </Link>
        </p>
        <p className="mx-auto mt-3 max-w-md text-xs leading-relaxed">
          {LEGAL_ENTITY} · Registered in England and Wales, company number{" "}
          {COMPANY_NUMBER} · Registered office: {REGISTERED_OFFICE}
        </p>
      </footer>
    </div>
  );
}
