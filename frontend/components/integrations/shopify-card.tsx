"use client";

import { AlertCircle, Link2, Loader2, RefreshCw, Unlink } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

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
import {
  useClaimShopifyInstall,
  useConnectShopify,
  useDisconnectShopify,
  useShopifyStatus,
} from "@/services/integrations";
import type { ShopifyConnection } from "@/types/api";

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

function ConnectionRow({
  connection,
  onDisconnect,
  onReconnect,
  disconnecting,
  reconnecting,
  disconnectError,
}: {
  connection: ShopifyConnection;
  onDisconnect: (storeId: string) => void;
  onReconnect: (shopDomain: string) => void;
  disconnecting: boolean;
  reconnecting: boolean;
  disconnectError: string | null;
}) {
  const busy = disconnecting || reconnecting;
  const canReconnect = connection.status !== "connected";

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
              Webhooks: {formatDate(connection.webhooksRegisteredAt)}
            </p>
          ) : connection.status === "connected" ? (
            <p className="text-amber-700 dark:text-amber-400">
              Webhooks not registered yet — reconnect if sync stalls.
            </p>
          ) : null}
        </div>
        <Badge variant={statusVariant(connection.status)}>
          {statusLabel(connection.status)}
        </Badge>
      </div>
      {connection.lastError ? (
        <p className="mt-2 text-destructive">{connection.lastError}</p>
      ) : null}
      {disconnectError ? (
        <p className="mt-2 text-destructive">{disconnectError}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
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
    </div>
  );
}

export function ShopifyCard() {
  const searchParams = useSearchParams();
  const { data, isPending, isError, refetch } = useShopifyStatus();
  const connect = useConnectShopify();
  const claim = useClaimShopifyInstall();
  const disconnect = useDisconnectShopify();
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
  const connecting = connect.isPending || claim.isPending;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Shopify</CardTitle>
          <Badge variant={connectedCount ? "success" : "secondary"}>
            {connectedCount
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
            disconnecting={disconnect.isPending}
            reconnecting={connect.isPending}
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
