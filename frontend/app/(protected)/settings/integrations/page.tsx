"use client";

import { CheckCircle2, Info, XCircle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AliExpressCard } from "@/components/integrations/aliexpress-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Integrations settings.
 *
 * AliExpress is real and functional. The other providers are listed as
 * unavailable rather than omitted, because knowing what is planned is useful —
 * but each says plainly that it cannot be connected, and none offers a button
 * that would do nothing.
 */

/** Result of an OAuth round trip, set by the callback redirect. */
const CALLBACK_MESSAGES: Record<
  string,
  { variant: "default" | "destructive"; title: string; body: string }
> = {
  connected: {
    variant: "default",
    title: "AliExpress connected",
    body: "Your AliExpress account is now linked to this workspace.",
  },
  denied: {
    variant: "destructive",
    title: "Authorization declined",
    body: "The request was declined on AliExpress, so nothing was connected.",
  },
  failed: {
    variant: "destructive",
    title: "Connection failed",
    body: "AliExpress could not be connected. Check the credentials and try again.",
  },
  invalid: {
    variant: "destructive",
    title: "Authorization expired",
    body: "That authorization request is no longer valid. Please start again.",
  },
};

const PLANNED_PROVIDERS = [
  { name: "Shopify", description: "Publish products and receive orders." },
  { name: "WooCommerce", description: "Sync your WordPress storefront." },
  { name: "eBay", description: "List and fulfil across eBay marketplaces." },
  { name: "Etsy", description: "Reach Etsy buyers with the same catalogue." },
  { name: "TikTok Shop", description: "Sell through TikTok's marketplace." },
] as const;

function CallbackBanner() {
  const searchParams = useSearchParams();
  const result = searchParams.get("aliexpress");

  if (!result) return null;

  const message = CALLBACK_MESSAGES[result];
  if (!message) return null;

  return (
    <Alert variant={message.variant}>
      {message.variant === "destructive" ? (
        <XCircle className="h-4 w-4" />
      ) : (
        <CheckCircle2 className="h-4 w-4" />
      )}
      <AlertTitle>{message.title}</AlertTitle>
      <AlertDescription>{message.body}</AlertDescription>
    </Alert>
  );
}

export default function IntegrationsPage() {
  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Integrations"
        description="Connect the suppliers and sales channels this workspace sells through."
      />

      {/* useSearchParams opts the subtree into client rendering, so Next
          requires a Suspense boundary for the static shell to build. */}
      <Suspense fallback={null}>
        <CallbackBanner />
      </Suspense>

      <section aria-label="Suppliers" className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Suppliers
        </h2>
        <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
          <AliExpressCard />
        </Suspense>
      </section>

      <section aria-label="Sales channels" className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Sales channels
        </h2>

        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            Sales channel integrations are not available yet. Only AliExpress can
            be connected in this release.
          </AlertDescription>
        </Alert>

        <div className="grid gap-4 sm:grid-cols-2">
          {PLANNED_PROVIDERS.map((provider) => (
            <Card key={provider.name} className="opacity-60">
              <CardHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <CardTitle className="text-base">{provider.name}</CardTitle>
                  {/* No connect button: a control that cannot work is worse
                      than no control at all. */}
                  <Badge variant="outline">Coming soon</Badge>
                </div>
                <CardDescription>{provider.description}</CardDescription>
              </CardHeader>
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
