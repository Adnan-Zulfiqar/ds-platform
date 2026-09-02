import Link from "next/link";

import {
  COMPANY_NUMBER,
  LEGAL_ENTITY,
  REGISTERED_OFFICE,
  TRADING_NAME,
} from "@/components/legal/company-disclosure";

/**
 * Footer for public pages that must carry the TikTok / ECR company disclosure
 * without requiring authentication or client-side bootstrap.
 */
export function PublicFooter() {
  return (
    <footer className="border-t border-border bg-muted/30">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-10 sm:px-6">
        <nav
          aria-label="Legal and account"
          className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-sm"
        >
          <Link
            href="/privacy"
            className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Privacy Policy
          </Link>
          <Link
            href="/terms"
            className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Terms of Service <span className="text-muted-foreground">(draft)</span>
          </Link>
          <Link
            href="/login"
            className="text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Sign in
          </Link>
          <Link
            href="/register"
            className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Create account
          </Link>
        </nav>

        <p
          className="mx-auto max-w-3xl text-center text-sm leading-relaxed text-muted-foreground"
          data-testid="company-disclosure"
        >
          {TRADING_NAME} is operated by {LEGAL_ENTITY}, a private limited company registered in
          England and Wales under company number {COMPANY_NUMBER}. Registered office:{" "}
          {REGISTERED_OFFICE}.
        </p>

        <p className="text-center text-xs text-muted-foreground">
          &copy; {new Date().getFullYear()} {LEGAL_ENTITY}
        </p>
      </div>
    </footer>
  );
}
