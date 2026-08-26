"use client";

import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AliExpressCard } from "@/components/integrations/aliexpress-card";
import { EbayCard } from "@/components/integrations/ebay-card";
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
  {
    variant: "default" | "destructive" | "warning";
    title: string;
    body: string;
  }
> = {
  connected: {
    variant: "default",
    title: "Shopify connected",
    body: "Your Shopify store is now linked to this workspace.",
  },
  // The store *is* connected and its token is valid, so this is not a failure
  // banner — but calling it a plain success is what previously left merchants
  // believing sync was working when no subscription had been created.
  connected_webhooks_degraded: {
    variant: "warning",
    title: "Connected — webhook setup incomplete",
    body: "Your store is linked, but DropPilot could not confirm its Shopify webhooks. Product, inventory and order updates may be missed until you retry webhook setup below. You do not need to disconnect.",
  },
  denied: {
    variant: "destructive",
    title: "Authorization declined",
    body: "The request was declined on Shopify, so nothing was connected.",
  },
  failed: {
    variant: "destructive",
    title: "Connection failed",
    body:
      "Shopify could not be connected. Confirm you used your *.myshopify.com domain and try again. If it keeps failing, ask your DropPilot operator to verify the app redirection URL.",
  },
  hmac: {
    variant: "destructive",
    title: "Shopify signature invalid",
    body:
      "The install or callback signature did not match. Ask your DropPilot operator to verify the Shopify app secret.",
  },
  state: {
    variant: "destructive",
    title: "Authorization expired",
    body: "That Shopify authorization request expired or was reused. Click Connect Shopify and try again.",
  },
  exchange: {
    variant: "destructive",
    title: "Token exchange failed",
    body:
      "Shopify accepted consent but rejected the code exchange. Ask your DropPilot operator to verify the app Client ID and secret belong to the same app.",
  },
  taken: {
    variant: "destructive",
    title: "Store already linked",
    body: "This Shopify store is already connected to another DropPilot workspace.",
  },
  claim_needed: {
    variant: "default",
    title: "Finish Shopify install",
    body: "Sign in to DropPilot if needed — we will continue the Shopify authorization for this workspace.",
  },
  invalid_shop: {
    variant: "destructive",
    title: "Invalid store domain",
    body: "Use a *.myshopify.com admin domain from Shopify Admin → Settings → Domains.",
  },
};

const EBAY_CALLBACK: Record<
  string,
  { variant: "default" | "destructive"; title: string; body: string }
> = {
  connected: {
    variant: "default",
    title: "eBay connected",
    body: "Your eBay seller account is now linked to this workspace.",
  },
  denied: {
    variant: "destructive",
    title: "Authorization declined",
    body: "The request was declined on eBay, so nothing was connected.",
  },
  invalid: {
    variant: "destructive",
    title: "Authorization expired",
    body: "That eBay authorization request expired or was already used. Click Connect eBay and try again.",
  },
  already_linked: {
    variant: "destructive",
    title: "eBay account already linked",
    body: "That eBay seller account is already connected to another DropPilot workspace. Disconnect it there first, then try again.",
  },
  failed: {
    variant: "destructive",
    title: "Connection failed",
    body: "eBay could not be connected. Please try again, and ask your DropPilot operator to check the eBay application settings if it keeps failing.",
  },
};

const PLANNED_PROVIDERS = [
  { name: "WooCommerce", description: "Sync your WordPress storefront." },
  { name: "Etsy", description: "Reach Etsy buyers with the same catalogue." },
  { name: "TikTok Shop", description: "Sell through TikTok's marketplace." },
] as const;

function CallbackBanner() {
  const searchParams = useSearchParams();
  const aliexpress = searchParams.get("aliexpress");
  const shopify = searchParams.get("shopify");
  const ebay = searchParams.get("ebay");

  const message =
    (aliexpress && ALIEXPRESS_CALLBACK[aliexpress]) ||
    (shopify && SHOPIFY_CALLBACK[shopify]) ||
    (ebay && EBAY_CALLBACK[ebay]) ||
    null;

  if (!message) return null;

  return (
    <Alert variant={message.variant}>
      {message.variant === "destructive" ? (
        <XCircle className="h-4 w-4" />
      ) : message.variant === "warning" ? (
        <AlertTriangle className="h-4 w-4" />
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

        <Suspense fallback={<Skeleton className="h-64 w-full rounded-lg" />}>
          <EbayCard />
        </Suspense>

        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            Additional sales channels remain planned. Shopify and eBay are
            available when the server has that provider&apos;s app credentials
            configured.
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
