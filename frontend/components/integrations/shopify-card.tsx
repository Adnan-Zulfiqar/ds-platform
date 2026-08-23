"use client";

import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Link2,
  Loader2,
  RefreshCw,
  Unlink,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { useAuth } from "@/providers/auth-provider";
import {
  useClaimShopifyInstall,
  useConnectShopify,
  useDisconnectShopify,
  useReconcileShopifyWebhooks,
  useShopifyStatus,
} from "@/services/integrations";
import type { ShopifyConnection, ShopifyWebhookReconcileResult } from "@/types/api";

function formatDate(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function statusLabel(status: string): string {
  switch (status) {
    case "connected":
      return "Connected";
    case "error":
      return "Error";
    case "pending":
      return "Pending";
    case "disconnected":
      return "Disconnected";
    default:
      return status;
  }
}

function statusVariant(
  status: string,
): "success" | "destructive" | "secondary" | "warning" {
  switch (status) {
    case "connected":
      return "success";
    case "error":
      return "destructive";
    case "pending":
      return "warning";
    default:
      return "secondary";
  }
}

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

function ConnectionRow({
  connection,
  onDisconnect,
  onReconnect,
  onRetryWebhooks,
  disconnecting,
  reconnecting,
  retrying,
  retryResult,
  retryError,
  canManage,
  disconnectError,
}: {
  connection: ShopifyConnection;
  onDisconnect: (storeId: string) => void;
  onReconnect: (shopDomain: string) => void;
  onRetryWebhooks: (storeId: string) => void;
  disconnecting: boolean;
  reconnecting: boolean;
  retrying: boolean;
  retryResult: ShopifyWebhookReconcileResult | null;
  retryError: string | null;
  canManage: boolean;
  disconnectError: string | null;
}) {
  const busy = disconnecting || reconnecting || retrying;
  const canReconnect = connection.status !== "connected";
  // Derived by the API from the same timestamp it returns, so the card cannot
  // reach a different verdict than the server did. This is the fix for a store
  // rendering as fully connected while no webhook had ever been registered.
  const degraded = connection.webhookHealth === "degraded";
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
    <div className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium">{connection.shopDomain}</p>
          <p className="text-muted-foreground">
            Connected: {formatDate(connection.connectedAt)}
          </p>
          <p className="text-muted-foreground">
            Last sync: {formatDate(connection.lastSyncAt)}
          </p>
          {connection.webhooksRegisteredAt ? (
            <p className="text-muted-foreground">
              Webhooks last confirmed:{" "}
              {formatDate(connection.webhooksRegisteredAt)}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* "Last confirmed", never "active". DropPilot cannot observe a
              subscription Shopify deletes on its own, so the honest claim is
              about the last successful confirmation and its date — which the
              line above carries — not about live provider state. */}
          {degraded ? (
            <Badge variant="warning">Webhooks incomplete</Badge>
          ) : connection.webhookHealth === "healthy" ? (
            <Badge variant="success">Webhooks last confirmed</Badge>
          ) : null}
          <Badge variant={statusVariant(connection.status)}>
            {statusLabel(connection.status)}
          </Badge>
        </div>
      </div>

      {degraded ? (
        <Alert variant="warning" className="mt-3">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            DropPilot could not confirm this store&rsquo;s Shopify webhooks on
            its most recent attempt.{" "}
            <strong>Product, inventory and order updates may be missed</strong>{" "}
            until setup completes. Your store stays connected — you do not need
            to disconnect.
          </AlertDescription>
        </Alert>
      ) : null}

      {/* One live region per connection. Polite rather than assertive: this
          reports the outcome of something the user just asked for, so it should
          not interrupt what they are already reading. */}
      <div
        ref={statusRef}
        role="status"
        aria-live="polite"
        tabIndex={-1}
        className="mt-2 outline-none"
        data-testid={`shopify-webhook-status-${connection.storeId}`}
      >
        {retrying ? (
          <p className="text-muted-foreground">Retrying webhook setup…</p>
        ) : retryError ? (
          <p className="text-destructive">{retryError}</p>
        ) : retryResult?.healthy ? (
          <p className="text-success">
            <CheckCircle2 className="mr-1 inline h-4 w-4" />
            Webhook setup confirmed
            {retryResult.webhooksRegisteredAt
              ? ` at ${formatDate(retryResult.webhooksRegisteredAt)}`
              : ""}
            . {retryResult.createdCount} created.
          </p>
        ) : retryResult ? (
          <p className="text-warning">
            Webhook setup is still incomplete — {unresolvedCount(retryResult)} of{" "}
            {retryResult.topics.length} topics unconfirmed. Try again in a
            moment; nothing was duplicated.
          </p>
        ) : null}
      </div>

      {connection.lastError ? (
        <p className="mt-2 text-destructive">{connection.lastError}</p>
      ) : null}
      {disconnectError ? (
        <p className="mt-2 text-destructive">{disconnectError}</p>
      ) : null}

      {canManage ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {degraded ? (
            <Button
              variant="default"
              size="sm"
              disabled={busy}
              aria-describedby={`shopify-webhook-status-${connection.storeId}`}
              onClick={() => onRetryWebhooks(connection.storeId)}
            >
              {retrying ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Retry webhook setup
            </Button>
          ) : null}
          {canReconnect ? (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => onReconnect(connection.shopDomain)}
            >
              {reconnecting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Reconnect
            </Button>
          ) : null}
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => onDisconnect(connection.storeId)}
          >
            {disconnecting ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Unlink className="mr-2 h-4 w-4" />
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
  const { data, isPending, isError, refetch } = useShopifyStatus();
  const connect = useConnectShopify();
  const claim = useClaimShopifyInstall();
  const disconnect = useDisconnectShopify();
  const reconcile = useReconcileShopifyWebhooks();
  const [retryTarget, setRetryTarget] = useState<string | null>(null);
  const [retryResults, setRetryResults] = useState<
    Record<string, ShopifyWebhookReconcileResult>
  >({});
  const [retryErrors, setRetryErrors] = useState<Record<string, string>>({});
  const [shop, setShop] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [disconnectErrors, setDisconnectErrors] = useState<Record<string, string>>(
    {},
  );
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
    window.location.assign(result.authorizationUrl);
  }

  async function handleDisconnect(storeId: string) {
    setDisconnectErrors((previous) => {
      const { [storeId]: _removed, ...rest } = previous;
      return rest;
    });
    try {
      await disconnect.mutateAsync(storeId);
    } catch (error) {
      setDisconnectErrors((previous) => ({
        ...previous,
        [storeId]:
          error instanceof ApiError
            ? error.message
            : "Could not disconnect this store.",
      }));
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
      setFormError(
        error instanceof ApiError
          ? error.message
          : "Could not start Shopify connection.",
      );
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

  if (isPending) {
    return <Skeleton className="h-64 w-full rounded-lg" />;
  }

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          Could not load Shopify status.{" "}
          <button type="button" className="underline" onClick={() => void refetch()}>
            Retry
          </button>
        </AlertDescription>
      </Alert>
    );
  }

  const connections = data?.connections ?? [];
  const configured = data?.configured ?? false;
  const connectedCount = connections.filter((c) => c.status === "connected").length;
  const degradedCount = connections.filter(
    (c) => c.webhookHealth === "degraded",
  ).length;
  const connecting = connect.isPending || claim.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Shopify</CardTitle>
          {/* A store whose webhooks were never confirmed is not "connected" in
              any sense a merchant cares about, so the summary badge refuses to
              say so while one is degraded. */}
          <Badge
            variant={
              degradedCount ? "warning" : connectedCount ? "success" : "secondary"
            }
          >
            {degradedCount
              ? `${degradedCount} needs webhook setup`
              : connectedCount
                ? `${connectedCount} connected`
                : connections.length
                  ? "Needs attention"
                  : "Not connected"}
          </Badge>
        </div>
        <CardDescription>
          Connect a Shopify store to publish products and import orders. You only
          enter the store domain — never an API key or token.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!configured ? (
          <Alert>
            <AlertDescription>
              Shopify OAuth is not configured on this server. Ask your DropPilot
              operator to enable the Shopify app credentials.
            </AlertDescription>
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
            <Loader2 className="h-4 w-4 animate-spin" />
            <AlertDescription>
              Continuing Shopify install
              {claimShop ? ` for ${claimShop}` : ""}…
            </AlertDescription>
          </Alert>
        ) : null}

        {connections.length === 0 && configured ? (
          <p className="text-sm text-muted-foreground">
            No Shopify stores linked to this workspace yet.
          </p>
        ) : null}

        {connections.map((connection) => (
          <ConnectionRow
            key={connection.id}
            connection={connection}
            canManage={canManage}
            disconnecting={disconnect.isPending}
            reconnecting={connect.isPending}
            retrying={retryTarget === connection.storeId}
            retryResult={retryResults[connection.storeId] ?? null}
            retryError={retryErrors[connection.storeId] ?? null}
            onRetryWebhooks={(storeId) => {
              void handleRetryWebhooks(storeId);
            }}
            disconnectError={disconnectErrors[connection.storeId] ?? null}
            onDisconnect={(storeId) => {
              void handleDisconnect(storeId);
            }}
            onReconnect={(shopDomain) => {
              void startOAuth(shopDomain).catch((error: unknown) => {
                setFormError(
                  error instanceof ApiError
                    ? error.message
                    : "Could not start Shopify reconnection.",
                );
              });
            }}
          />
        ))}

        {formError && !dialogOpen ? (
          <p className="text-sm text-destructive">{formError}</p>
        ) : null}
      </CardContent>
      <CardFooter>
        <Button
          onClick={() => {
            setFormError(null);
            if (prefillShop) {
              setShop(prefillShop);
            }
            setDialogOpen(true);
          }}
          disabled={!configured || connecting}
        >
          {connecting ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Link2 className="mr-2 h-4 w-4" />
          )}
          Connect Shopify
        </Button>
      </CardFooter>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Connect Shopify store</DialogTitle>
            <DialogDescription>
              Enter your <span className="font-medium">*.myshopify.com</span> admin
              domain. DropPilot redirects you to Shopify to approve access — you
              never paste API keys or tokens here.
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
              From Shopify Admin → Settings → Domains. Custom domains like
              store.com will not work.
            </p>
            {formError ? <p className="text-sm text-destructive">{formError}</p> : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button disabled={connecting} onClick={() => void handleDialogConnect()}>
              {connecting ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : null}
              Continue to Shopify
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
