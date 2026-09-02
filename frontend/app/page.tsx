import type { Metadata } from "next";
import Link from "next/link";

import { PublicFooter } from "@/components/marketing/public-footer";
import { TRADING_NAME } from "@/components/legal/company-disclosure";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Dropshipping automation for multi-channel sellers",
  description:
    "DropPilot AI helps merchants import supplier catalogues, prepare product drafts, and manage orders across connected marketplaces — with tenant isolation and audit-friendly controls.",
  /**
   * The homepage is the one public marketing surface that should be discoverable.
   * Everything under authentication keeps the root layout's `noindex`.
   */
  robots: { index: true, follow: true },
};

const FEATURES = [
  {
    title: "Import with guardrails",
    body: "Bring supplier listings into draft products with destination, currency and freight checks — unknown shipping is surfaced, not silently treated as free.",
  },
  {
    title: "Edit before you publish",
    body: "Rich descriptions, variants, pricing rules and SEO fields in one workspace. Nothing goes live until you confirm readiness.",
  },
  {
    title: "Connect stores safely",
    body: "OAuth connections for marketplaces are scoped per tenant. Credentials are encrypted at rest and never echoed back to the browser.",
  },
  {
    title: "Operate with visibility",
    body: "Orders, inventory signals and automation tasks in one dashboard — designed for operators who need to see what changed and why.",
  },
] as const;

export default function HomePage() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="border-b border-border">
        <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <p className="text-lg font-semibold tracking-tight">{TRADING_NAME}</p>
          <nav aria-label="Account" className="flex items-center gap-2 sm:gap-3">
            <Button variant="ghost" asChild size="sm">
              <Link href="/login">Sign in</Link>
            </Button>
            <Button asChild size="sm">
              <Link href="/register">Get started</Link>
            </Button>
          </nav>
        </div>
      </header>

      <main id="main-content" className="flex-1">
        <section className="border-b border-border bg-muted/20">
          <div className="mx-auto w-full max-w-5xl px-4 py-14 sm:px-6 sm:py-20">
            <div className="max-w-2xl space-y-6">
              <h1 className="text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">
                Run your dropshipping operation with clarity, not guesswork
              </h1>
              <p className="text-base leading-relaxed text-muted-foreground sm:text-lg">
                {TRADING_NAME} is a multi-tenant workspace for importing supplier products,
                preparing drafts, and coordinating orders across connected channels. It is built
                for operators who need predictable controls — not a black box that publishes while
                you are not looking.
              </p>
              <div className="flex flex-wrap gap-3">
                <Button asChild size="lg">
                  <Link href="/register">Create a free account</Link>
                </Button>
                <Button asChild variant="outline" size="lg">
                  <Link href="/login">Sign in to your workspace</Link>
                </Button>
              </div>
              <p className="text-sm text-muted-foreground">
                Marketplace connections require your own seller accounts. {TRADING_NAME} does not
                sell inventory and does not guarantee marketplace approval.
              </p>
            </div>
          </div>
        </section>

        <section aria-labelledby="features-heading" className="mx-auto w-full max-w-5xl px-4 py-14 sm:px-6">
          <h2 id="features-heading" className="text-2xl font-semibold tracking-tight">
            What you can do today
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Capabilities vary by integration and account status. The product is under active
            development — connect only the channels you intend to use.
          </p>
          <ul className="mt-8 grid gap-6 sm:grid-cols-2">
            {FEATURES.map((feature) => (
              <li
                key={feature.title}
                className="rounded-lg border border-border bg-card p-5 shadow-sm"
              >
                <h3 className="font-semibold text-foreground">{feature.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{feature.body}</p>
              </li>
            ))}
          </ul>
        </section>
      </main>

      <PublicFooter />
    </div>
  );
}
