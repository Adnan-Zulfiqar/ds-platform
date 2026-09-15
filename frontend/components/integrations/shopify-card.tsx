"use client";

import { AlertCircle, AlertTriangle, CheckCircle2, Link2, Loader2, RefreshCw, Unlink } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ChannelCard, formatChannelDate } from "@/components/integrations/channel-card";
import { ChannelStatusBadge } from "@/components/integrations/channel-status-badge";
import { DisconnectDialog } from "@/components/integrations/disconnect-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import { deriveShopifyChannel, type ShopifyConnectionState } from "@/lib/channel-state";
import { useAuth } from "@/providers/auth-provider";
import {
  useClaimShopifyInstall,
  useConnectShopify,
  useDisconnectShopify,
  useReconcileShopifyWebhooks,
  useShopifyStatus,
} from "@/services/integrations";
import type { ShopifyWebhookReconcileResult } from "@/types/api";

/** Client-side hint only — server still normalises and validates. */
function normaliseShopInput(raw: string): string {
  let value = raw.trim().toLowerCase();
  value = value.replace(/^https?:\/\//, "");
  value = value.split("/")[0] ?? value;
  value = value.replace(/^www\./, "");
  if (value && !value.includes(".")) {
    value = `${value}.myshopify.com`;
  }
  return value;
}

/** How many topics a reconciliation result still could not confirm. */
function unresolvedCount(result: ShopifyWebhookReconcileResult): number {
  return result.topics.filter(
    (topic) => topic.status !== "already_present" && topic.status !== "created",
  ).length;
}

const DISCONNECT_CONSEQUENCES = [
  "DropPilot's access token and webhooks for this store are removed at Shopify.",
  "The store stays listed under Stores as Disconnected; products and listings recorded in DropPilot are kept.",
  "Products already on Shopify stay on Shopify — nothing is deleted from your store.",
  "You can reconnect the same store later from this page.",
];

function ConnectionRow({
  row,
  canManage,
  disconnecting,
  reconnecting,
  retrying,
  retryResult,
  retryError,
  onDisconnect,
  onReconnect,
  onRetryWebhooks,
}: {
  row: ShopifyConnectionState;
  canManage: boolean;
  disconnecting: boolean;
  reconnecting: boolean;
  retrying: boolean;
  retryResult: ShopifyWebhookReconcileResult | null;
  retryError: string | null;
  onDisconnect: () => void;
  onReconnect: () => void;
  onRetryWebhooks: () => void;
}) {
  const { connection } = row;
  const busy = disconnecting || reconnecting || retrying;
  const degraded = row.webhookLabel === "Webhooks incomplete";
  const statusRef = useRef<HTMLDivElement | null>(null);

  // Move focus to the outcome once a retry settles, so a keyboard or screen
  // reader user is taken to the answer rather than left on a button whose
  // label did not change.
  useEffect(() => {
    if (retryResult || retryError) {
      statusRef.current?.focus();
    }
  }, [retryResult, retryError]);

  return (
    <div className="rounded-md border p-3 text-sm" data-testid={`shopify-connection-${connection.storeId}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="break-all font-medium">{connection.shopDomain}</p>
          <p className="mt-1 text-muted-foreground">{row.detail}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* "Last confirmed", never "active". DropPilot cannot observe a
              subscription Shopify deletes on its own, so the honest claim is
              about the last successful confirmation and its date. */}
          {row.webhookLabel ? (
            <Badge variant={degraded ? "warning" : "success"}>{row.webhookLabel}</Badge>
          ) : null}
          <ChannelStatusBadge state={row} data-testid={`shopify-connection-status-${connection.storeId}`} />
        </div>
      </div>

      <div className="mt-2 space-y-0.5 text-muted-foreground">
        <p>Connected: {formatChannelDate(connection.connectedAt)}</p>
        <p>Last sync: {formatChannelDate(connection.lastSyncAt)}</p>
        {connection.webhooksRegisteredAt ? (
          <p>Webhooks last confirmed: {formatChannelDate(connection.webhooksRegisteredAt)}</p>
        ) : null}
      </div>

      {degraded ? (
        <Alert variant="warning" className="mt-3">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            DropPilot could not confirm this store&rsquo;s Shopify webhooks on its most recent attempt.{" "}
            <strong>Product, inventory and order updates may be missed</strong> until setup completes. Your store
            stays connected — you do not need to disconnect.
          </AlertDescription>
        </Alert>
      ) : null}

      {/* One live region per connection. Polite rather than assertive: this
          reports the outcome of something the user just asked for, so it
          should not interrupt what they are already reading. */}
      <div
        ref={statusRef}
        role="status"
        aria-live="polite"
        tabIndex={-1}
        id={`shopify-webhook-status-${connection.storeId}`}
        className="mt-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-testid={`shopify-webhook-status-${connection.storeId}`}
      >
        {retrying ? (
          <p className="text-muted-foreground">Retrying webhook setup…</p>
        ) : retryError ? (
          <p className="text-destructive">{retryError}</p>
        ) : retryResult?.healthy ? (
          <p className="text-success">
            <CheckCircle2 className="mr-1 inline h-4 w-4" aria-hidden="true" />
            Webhook setup confirmed
            {retryResult.webhooksRegisteredAt ? ` at ${formatChannelDate(retryResult.webhooksRegisteredAt)}` : ""}.{" "}
            {retryResult.createdCount} created.
          </p>
        ) : retryResult ? (
          <p className="text-warning">
            Webhook setup is still incomplete — {unresolvedCount(retryResult)} of {retryResult.topics.length} topics
            unconfirmed. Try again in a moment; nothing was duplicated.
          </p>
        ) : null}
      </div>

      {canManage ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {row.actions.includes("retry-webhooks") ? (
            <Button
              variant="default"
              size="sm"
              className="min-h-11 sm:min-h-9"
              disabled={busy}
              aria-describedby={`shopify-webhook-status-${connection.storeId}`}
              onClick={onRetryWebhooks}
            >
              {retrying ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
              )}
              Retry webhook setup
            </Button>
          ) : null}
          {row.actions.includes("reconnect") ? (
            <Button variant={row.kind === "reconnect-required" ? "default" : "outline"} size="sm" className="min-h-11 sm:min-h-9" disabled={busy} onClick={onReconnect}>
              {reconnecting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
              )}
              Reconnect
            </Button>
          ) : null}
          <Button variant="outline" size="sm" className="min-h-11 sm:min-h-9" disabled={busy} onClick={onDisconnect}>
            {disconnecting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : (
              <Unlink className="mr-2 h-4 w-4" aria-hidden="true" />
            )}
            Disconnect
          </Button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          <Badge variant="outline" className="mr-2">
            Read only
          </Badge>
          Your role can view this connection. Ask an administrator to
          {degraded ? " retry webhook setup" : " make changes"}.
        </p>
      )}
    </div>
  );
}

export function ShopifyCard() {
  const searchParams = useSearchParams();
  const { hasRole } = useAuth();
  const canManage = hasRole("owner") || hasRole("admin");
  const statusQuery = useShopifyStatus();
  const channel = deriveShopifyChannel(statusQuery);
  const connect = useConnectShopify();
  const claim = useClaimShopifyInstall();
  const disconnect = useDisconnectShopify();
  const reconcile = useReconcileShopifyWebhooks();
  const [retryTarget, setRetryTarget] = useState<string | null>(null);
  const [retryResults, setRetryResults] = useState<Record<string, ShopifyWebhookReconcileResult>>({});
  const [retryErrors, setRetryErrors] = useState<Record<string, string>>({});
  const [shop, setShop] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [disconnectTarget, setDisconnectTarget] = useState<ShopifyConnectionState | null>(null);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);
  const [claimError, setClaimError] = useState<string | null>(null);
  const [claimStarted, setClaimStarted] = useState(false);

  const installToken = searchParams.get("install_token");
  const claimShop = searchParams.get("shop");
  const shopifyFlag = searchParams.get("shopify");
  const prefillShop = searchParams.get("shop");

  async function startOAuth(shopInput: string) {
    setFormError(null);
    const normalised = normaliseShopInput(shopInput);
    if (!normalised) {
      setFormError("Enter your Shopify store domain.");
      return;
    }
    const result = await connect.mutateAsync({ shop: normalised });
    // Full navigation: the destination is Shopify's consent page.
    window.location.assign(result.authorizationUrl);
  }

  async function confirmDisconnect() {
    if (!disconnectTarget) return;
    setDisconnectError(null);
    try {
      await disconnect.mutateAsync(disconnectTarget.connection.storeId);
      setDisconnectTarget(null);
    } catch (error) {
      setDisconnectError(
        error instanceof ApiError ? error.message : "Could not disconnect this store. Please try again.",
      );
    }
  }

  /**
   * Retry webhook registration for one store.
   *
   * Only ever called from the button. There is no effect that fires it on
   * mount and no automatic re-attempt on failure: the endpoint is idempotent,
   * but a self-retrying client would turn one merchant click into a loop of
   * listings against Shopify.
   */
  async function handleRetryWebhooks(storeId: string) {
    setRetryTarget(storeId);
    setRetryErrors((previous) => {
      const { [storeId]: _removed, ...rest } = previous;
      return rest;
    });
    setRetryResults((previous) => {
      const { [storeId]: _removed, ...rest } = previous;
      return rest;
    });
    try {
      const result = await reconcile.mutateAsync(storeId);
      setRetryResults((previous) => ({ ...previous, [storeId]: result }));
    } catch (error) {
      setRetryErrors((previous) => ({
        ...previous,
        [storeId]:
          error instanceof ApiError
            ? error.message
            : "Could not retry webhook setup. Please try again in a moment.",
      }));
    } finally {
      setRetryTarget(null);
    }
  }

  async function handleDialogConnect() {
    try {
      await startOAuth(shop);
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "Could not start Shopify connection.");
    }
  }

  useEffect(() => {
    if (shopifyFlag !== "claim_needed" || !installToken || claimStarted) {
      return;
    }
    setClaimStarted(true);
    void (async () => {
      try {
        const result = await claim.mutateAsync({ installToken });
        window.location.assign(result.authorizationUrl);
      } catch (error) {
        setClaimError(
          error instanceof ApiError
            ? error.message
            : "Could not continue the Shopify install for this workspace.",
        );
      }
    })();
  }, [shopifyFlag, installToken, claimStarted, claim]);

  useEffect(() => {
    if (prefillShop && shopifyFlag !== "claim_needed") {
      setShop(prefillShop);
    }
  }, [prefillShop, shopifyFlag]);

  const connecting = connect.isPending || claim.isPending;
  const loading = channel.kind === "checking";

  return (
    <>
      <ChannelCard
        id="shopify"
        name="Shopify"
        description="Publish products to a Shopify store and receive its inventory and order updates. You only enter the store domain — never an API key or token."
        state={channel}
        loading={loading}
        readOnly={!loading && !canManage && channel.kind !== "unavailable" && channel.connections.length === 0}
        readOnlyHint="Your role can view Shopify connections. Ask an administrator to connect a store."
        actions={
          channel.kind === "unavailable" ? (
            <Button variant="outline" className="min-h-11 sm:min-h-9" onClick={() => void statusQuery.refetch()}>
              <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
              Try again
            </Button>
          ) : canManage ? (
            <Button
              className="min-h-11 sm:min-h-9"
              onClick={() => {
                setFormError(null);
                if (prefillShop) setShop(prefillShop);
                setDialogOpen(true);
              }}
              disabled={!channel.canConnect || connecting}
              aria-describedby={channel.kind === "setup-unavailable" ? "channel-shopify-detail" : undefined}
            >
              {connecting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <Link2 className="mr-2 h-4 w-4" aria-hidden="true" />
              )}
              {channel.connections.length > 0 ? "Connect another store" : "Connect Shopify"}
            </Button>
          ) : undefined
        }
      >
        {channel.kind === "unavailable" ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>Could not load Shopify status.</AlertDescription>
          </Alert>
        ) : null}

        {claimError ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{claimError}</AlertDescription>
          </Alert>
        ) : null}

        {shopifyFlag === "claim_needed" && claim.isPending ? (
          <Alert>
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            <AlertDescription>
              Continuing Shopify install
              {claimShop ? ` for ${claimShop}` : ""}…
            </AlertDescription>
          </Alert>
        ) : null}

        {channel.connections.map((row) => (
          <ConnectionRow
            key={row.connection.id}
            row={row}
            canManage={canManage}
            disconnecting={disconnect.isPending && disconnectTarget?.connection.storeId === row.connection.storeId}
            reconnecting={connect.isPending}
            retrying={retryTarget === row.connection.storeId}
            retryResult={retryResults[row.connection.storeId] ?? null}
            retryError={retryErrors[row.connection.storeId] ?? null}
            onRetryWebhooks={() => {
              void handleRetryWebhooks(row.connection.storeId);
            }}
            onDisconnect={() => {
              setDisconnectError(null);
              setDisconnectTarget(row);
            }}
            onReconnect={() => {
              void startOAuth(row.connection.shopDomain).catch((error: unknown) => {
                setFormError(
                  error instanceof ApiError ? error.message : "Could not start Shopify reconnection.",
                );
              });
            }}
          />
        ))}

        {formError && !dialogOpen ? (
          <p className="text-sm text-destructive" role="alert">
            {formError}
          </p>
        ) : null}
      </ChannelCard>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Connect Shopify store</DialogTitle>
            <DialogDescription>
              Enter your <span className="font-medium">*.myshopify.com</span> admin domain. DropPilot redirects
              you to Shopify to approve access — you never paste API keys or tokens here.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="shopify-shop">Store domain</Label>
            <Input
              id="shopify-shop"
              placeholder="your-store.myshopify.com"
              value={shop}
              onChange={(event) => setShop(event.target.value)}
              disabled={connecting}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void handleDialogConnect();
                }
              }}
            />
            <p className="text-xs text-muted-foreground">
              From Shopify Admin → Settings → Domains. Custom domains like store.com will not work.
            </p>
            {formError ? (
              <p className="text-sm text-destructive" role="alert">
                {formError}
              </p>
            ) : null}
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" className="min-h-11 sm:min-h-9" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button className="min-h-11 sm:min-h-9" disabled={connecting} onClick={() => void handleDialogConnect()}>
              {connecting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : null}
              Continue to Shopify
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DisconnectDialog
        id="shopify"
        open={disconnectTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDisconnectTarget(null);
        }}
        provider="Shopify"
        identity={disconnectTarget?.connection.shopDomain ?? null}
        consequences={DISCONNECT_CONSEQUENCES}
        pending={disconnect.isPending}
        error={disconnectError}
        onConfirm={() => void confirmDisconnect()}
      />
    </>
  );
}
