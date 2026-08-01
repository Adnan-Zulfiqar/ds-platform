"use client";

import { CheckCircle2, Info, XCircle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AliExpressCard } from "@/components/integrations/aliexpress-card";
import { ShopifyCard } from "@/components/integrations/shopify-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";

const ALIEXPRESS_CALLBACK: Record<
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

const SHOPIFY_CALLBACK: Record<
  string,
  { variant: "default" | "destructive"; title: string; body: string }
> = {
  connected: {
    variant: "default",
    title: "Shopify connected",
    body: "Your Shopify store is now linked to this workspace.",
  },
  denied: {
    variant: "destructive",
    title: "Authorization declined",
    body: "The request was declined on Shopify, so nothing was connected.",
  },
  failed: {
    variant: "destructive",
    title: "Connection failed",
    body: "Shopify could not be connected. Check the app credentials and try again.",
  },
};

const PLANNED_PROVIDERS = [
  { name: "WooCommerce", description: "Sync your WordPress storefront." },
  { name: "eBay", description: "List and fulfil across eBay marketplaces." },
  { name: "Etsy", description: "Reach Etsy buyers with the same catalogue." },
  { name: "TikTok Shop", description: "Sell through TikTok's marketplace." },
] as const;

function CallbackBanner() {
  const searchParams = useSearchParams();
  const aliexpress = searchParams.get("aliexpress");
  const shopify = searchParams.get("shopify");

  const message =
    (aliexpress && ALIEXPRESS_CALLBACK[aliexpress]) ||
    (shopify && SHOPIFY_CALLBACK[shopify]) ||
    null;

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

        <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
          <ShopifyCard />
        </Suspense>

        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            Additional sales channels remain planned. Shopify is available when
            the server has Shopify app credentials configured.
          </AlertDescription>
        </Alert>

        <div className="grid gap-4 sm:grid-cols-2">
          {PLANNED_PROVIDERS.map((provider) => (
            <Card key={provider.name} className="opacity-60">
              <CardHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <CardTitle className="text-base">{provider.name}</CardTitle>
                  <Badge variant="secondary">Coming soon</Badge>
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
