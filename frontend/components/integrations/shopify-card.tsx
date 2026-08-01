"use client";

import { AlertCircle, Link2, Loader2, Unlink } from "lucide-react";
import { useState } from "react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
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

function ConnectionRow({
  connection,
  onDisconnect,
  disconnecting,
}: {
  connection: ShopifyConnection;
  onDisconnect: (storeId: string) => void;
  disconnecting: boolean;
}) {
  return (
    <div className="rounded-md border p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium">{connection.shopDomain}</p>
          <p className="text-muted-foreground">
            Last sync: {formatDate(connection.lastSyncAt)}
          </p>
        </div>
        <Badge
          variant={connection.status === "connected" ? "success" : "destructive"}
        >
          {connection.status}
        </Badge>
      </div>
      {connection.lastError ? (
        <p className="mt-2 text-destructive">{connection.lastError}</p>
      ) : null}
      <Button
        variant="outline"
        size="sm"
        className="mt-3"
        disabled={disconnecting}
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
  );
}

export function ShopifyCard() {
  const { data, isPending, isError, refetch } = useShopifyStatus();
  const connect = useConnectShopify();
  const disconnect = useDisconnectShopify();
  const [shop, setShop] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  async function handleConnect() {
    setFormError(null);
    const trimmed = shop.trim();
    if (!trimmed) {
      setFormError("Enter your Shopify store domain.");
      return;
    }
    try {
      const result = await connect.mutateAsync({ shop: trimmed });
      window.location.assign(result.authorizationUrl);
    } catch (error) {
      setFormError(
        error instanceof ApiError
          ? error.message
          : "Could not start Shopify connection.",
      );
    }
  }

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

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Shopify</CardTitle>
          <Badge variant={connections.length ? "success" : "secondary"}>
            {connections.length ? `${connections.length} connected` : "Not connected"}
          </Badge>
        </div>
        <CardDescription>
          Connect a Shopify store to publish products and import orders.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!configured ? (
          <Alert>
            <AlertDescription>
              Shopify is not configured on this server. Set SHOPIFY_API_KEY and
              SHOPIFY_API_SECRET to enable OAuth.
            </AlertDescription>
          </Alert>
        ) : null}

        {connections.map((connection) => (
          <ConnectionRow
            key={connection.id}
            connection={connection}
            disconnecting={disconnect.isPending}
            onDisconnect={(storeId) => {
              void disconnect.mutateAsync(storeId);
            }}
          />
        ))}

        <div className="space-y-2">
          <Label htmlFor="shopify-shop">Store domain</Label>
          <Input
            id="shopify-shop"
            placeholder="your-store.myshopify.com"
            value={shop}
            onChange={(event) => setShop(event.target.value)}
            disabled={!configured || connect.isPending}
          />
          <p className="text-xs text-muted-foreground">
            Use the <span className="font-medium">*.myshopify.com</span> domain from
            Shopify Admin → Settings → Domains — not a custom domain like
            store.com. The Shopify app Allowed redirection URL must match
            SHOPIFY_CALLBACK_URL exactly.
          </p>
          {formError ? <p className="text-sm text-destructive">{formError}</p> : null}
        </div>
      </CardContent>
      <CardFooter>
        <Button
          onClick={() => void handleConnect()}
          disabled={!configured || connect.isPending}
        >
          {connect.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Link2 className="mr-2 h-4 w-4" />
          )}
          Connect Shopify
        </Button>
      </CardFooter>
    </Card>
  );
}
