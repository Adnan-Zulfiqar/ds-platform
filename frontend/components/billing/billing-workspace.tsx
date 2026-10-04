"use client";

import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import {
  type BillingStatus,
  type Plan,
  useBillingStatus,
  useChangePlan,
  useOpenBillingPortal,
  useStartCheckout,
  useSyncBilling,
} from "@/services/billing";

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

function summary(status: BillingStatus): { label: string; tone: "success" | "warning" | "destructive" } {
  if (status.paid) {
    const plan = status.plans.find((p) => p.key === status.plan);
    return { label: `${plan?.name ?? "Paid"} plan · ${status.status}`, tone: "success" };
  }
  if (status.onTrial) return { label: "Free trial", tone: "warning" };
  return { label: "No active plan", tone: "destructive" };
}

/**
 * Track E6c: Settings → Billing. Stripe is the source of truth; this page
 * only shows what the server mirrors and hands off to Stripe-hosted pages
 * for payment. Only the owner sees the actions (the server enforces it too).
 *
 * `checkout` is the query value Stripe returns with; on `success` the page
 * asks the server to re-read the subscription once, so the new plan shows
 * even if the webhook is late.
 */
export function BillingWorkspace({ checkout }: { checkout: string | null }) {
  const { data, isLoading, isError, refetch } = useBillingStatus();
  const { hasRole } = useAuth();
  const isOwner = hasRole("owner");
  const startCheckout = useStartCheckout();
  const portal = useOpenBillingPortal();
  const change = useChangePlan();
  const sync = useSyncBilling();
  const synced = useRef(false);
  const [aiAddon, setAiAddon] = useState<boolean | null>(null);

  useEffect(() => {
    if (checkout === "success" && !synced.current) {
      synced.current = true;
      sync.mutate();
    }
  }, [checkout, sync]);

  if (isError) {
    return <ErrorState title="Could not load billing" onRetry={() => void refetch()} />;
  }
  if (isLoading || !data) return <Skeleton className="h-64 w-full" />;

  if (!data.configured) {
    return (
      <Alert>
        <AlertDescription>
          Billing is not set up on this server, so every feature is available without a plan.
        </AlertDescription>
      </Alert>
    );
  }

  const withAi = aiAddon ?? data.aiAddon;
  const head = summary(data);
  const usedPct = Math.min(100, Math.round((data.listingsUsed / Math.max(1, data.listingLimit)) * 100));
  const busy = startCheckout.isPending || portal.isPending || change.isPending;
  const failure = startCheckout.error ?? portal.error ?? change.error ?? sync.error;

  function choose(plan: Plan) {
    const choice = { plan: plan.key, aiAddon: withAi };
    if (data?.paid) {
      change.mutate(choice);
    } else {
      startCheckout.mutate(choice, { onSuccess: (url) => window.location.assign(url) });
    }
  }

  return (
    <div className="space-y-6">
      {checkout === "success" && (
        <p role="status" className="text-sm text-muted-foreground">
          {sync.isPending ? "Confirming your subscription…" : "Thanks — your subscription is updated."}
        </p>
      )}
      {checkout === "cancelled" && (
        <p role="status" className="text-sm text-muted-foreground">
          Checkout was cancelled. Nothing was charged.
        </p>
      )}

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-base">Current plan</CardTitle>
            <Badge variant={head.tone} data-testid="billing-state">
              {head.label}
            </Badge>
            {data.paid && data.aiAddon && <Badge variant="secondary">Unlimited AI</Badge>}
          </div>
          <CardDescription>
            {data.onTrial && !data.paid && <>Your free trial ends {formatDateTime(data.trialEndsAt)}. </>}
            {!data.onTrial && !data.paid && (
              <>Your trial has ended. Existing listings and orders keep syncing; choose a plan to import and publish again. </>
            )}
            {data.paid && data.currentPeriodEnd && (
              <>
                {data.cancelAtPeriodEnd ? "Ends" : "Renews"} {formatDateTime(data.currentPeriodEnd)}.{" "}
              </>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <div className="flex justify-between text-sm">
              <span>Listings</span>
              <span data-testid="billing-usage">
                {data.listingsUsed} / {data.listingLimit}
              </span>
            </div>
            <div className="h-2 rounded-full bg-muted" aria-hidden="true">
              <div
                className={cn("h-2 rounded-full", usedPct >= 100 ? "bg-destructive" : "bg-primary")}
                style={{ width: `${usedPct}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Each product counts once per store for every variant it has.
            </p>
          </div>
          {isOwner && data.hasCustomer && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => portal.mutate(undefined, { onSuccess: (url) => window.location.assign(url) })}
            >
              Payment method and invoices
            </Button>
          )}
        </CardContent>
      </Card>

      {failure && (
        <Alert variant="destructive">
          <AlertDescription>{errorText(failure)}</AlertDescription>
        </Alert>
      )}

      {!isOwner && (
        <p className="text-sm text-muted-foreground">Only the workspace owner can change the plan.</p>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="h-4 w-4"
          checked={withAi}
          disabled={!isOwner}
          onChange={(event) => setAiAddon(event.target.checked)}
        />
        Add unlimited AI (titles, descriptions, image analysis)
      </label>

      <div className="grid gap-4 md:grid-cols-3" data-testid="billing-plans">
        {data.plans.map((plan) => {
          const current = data.paid && data.plan === plan.key;
          const unchanged = current && data.aiAddon === withAi;
          const total = plan.priceUsd + (withAi ? plan.aiAddonUsd : 0);
          return (
            <Card key={plan.key} className={cn(current && "border-primary")}>
              <CardHeader>
                <CardTitle className="text-base">{plan.name}</CardTitle>
                <CardDescription>
                  <span className="text-2xl font-semibold text-foreground">${total}</span> / month
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <ul className="space-y-1">
                  <li>{plan.listingLimit} listings</li>
                  <li>AI add-on: ${plan.aiAddonUsd} / month</li>
                </ul>
                {isOwner && (
                  <Button className="w-full" disabled={busy || unchanged} onClick={() => choose(plan)}>
                    {unchanged ? "Current plan" : data.paid ? `Switch to ${plan.name}` : `Choose ${plan.name}`}
                  </Button>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
