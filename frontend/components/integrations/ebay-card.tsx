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
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  useConnectEbay,
  useDisconnectEbay,
  useEbayStatus,
} from "@/services/integrations";
import type { EbayConnection, EbayConnectionStatus } from "@/types/api";

/**
 * eBay seller connection card.
 *
 * Merchants authorize DropPilot's own eBay application against their seller
 * account. They never enter a client id, certificate id or RuName — those live
 * in server environment configuration, and a field asking for them would invite
 * a merchant to paste a credential into a form.
 *
 * Status is always the server's. `connected` in particular is computed there,
 * because a connection awaiting reconnection is *not* connected and a client
 * that inferred it from `status` alone would get that wrong.
 */

const STATUS_LABELS: Record<EbayConnectionStatus, string> = {
  pending: "Awaiting authorization",
  connected: "Connected",
  reconnect_required: "Reconnection required",
  error: "Connection error",
};

/**
 * Why a connection went stale, in the merchant's terms.
 *
 * The server sends a stable machine code and this is where it becomes English.
 * Doing it the other way round — sending prose the API had to keep stable —
 * would make every wording change a breaking API change.
 */
const RECONNECT_REASONS: Record<string, string> = {
  refresh_token_revoked:
    "eBay ended the authorization. This normally happens after an eBay password or username change, and reconnecting is the only way to restore it.",
  no_refresh_token:
    "eBay did not return a renewable authorization, so DropPilot cannot keep the connection alive. Reconnect to issue a new one.",
};

function StatusBadge({
  connection,
  configured,
}: {
  connection: EbayConnection | null;
  configured: boolean;
}) {
  if (!configured) {
    return <Badge variant="secondary">Unavailable</Badge>;
  }
  if (!connection) {
    return <Badge variant="secondary">Not connected</Badge>;
  }

  const variant =
    connection.status === "connected" && !connection.needsReconnect
      ? "success"
      : connection.status === "pending"
        ? "warning"
        : "destructive";

  return <Badge variant={variant}>{STATUS_LABELS[connection.status]}</Badge>;
}

function formatDate(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** `EBAY_GB` reads like a database value. `United Kingdom` reads like a place. */
const MARKETPLACE_NAMES: Record<string, string> = {
  EBAY_US: "United States",
  EBAY_GB: "United Kingdom",
  EBAY_DE: "Germany",
  EBAY_AU: "Australia",
  EBAY_CA: "Canada",
  EBAY_FR: "France",
  EBAY_IT: "Italy",
  EBAY_ES: "Spain",
  EBAY_IE: "Ireland",
  EBAY_NL: "Netherlands",
};

function marketplaceLabel(id: string | null): string {
  if (!id) return "Unknown";
  return MARKETPLACE_NAMES[id] ?? id;
}

export function EbayCard() {
  const { data, isPending, isError, refetch } = useEbayStatus();
  const connect = useConnectEbay();
  const disconnect = useDisconnectEbay();
  const [actionError, setActionError] = useState<string | null>(null);

  const connection = data?.connection ?? null;
  // Default to `true` while loading so the button does not flicker through a
  // disabled "unavailable" state on every page load.
  const configured = data?.configured ?? true;
  const busy = connect.isPending || disconnect.isPending;

  async function handleConnect() {
    setActionError(null);
    try {
      const authorization = await connect.mutateAsync();
      // Full navigation: the destination is eBay, outside this application.
      window.location.assign(authorization.authorizationUrl);
    } catch (error) {
      setActionError(
        error instanceof ApiError
          ? error.message
          : "Could not start the connection. Please try again.",
      );
    }
  }

  async function handleDisconnect() {
    setActionError(null);
    try {
      await disconnect.mutateAsync();
    } catch (error) {
      setActionError(
        error instanceof ApiError
          ? error.message
          : "Could not disconnect. Please try again.",
      );
    }
  }

  return (
    <Card data-testid="ebay-card">
      <CardHeader className="flex-row items-start justify-between space-y-0 gap-4">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-base">eBay</CardTitle>
            {isPending ? (
              <Skeleton className="h-5 w-24 rounded-full" />
            ) : (
              <StatusBadge connection={connection} configured={configured} />
            )}
          </div>
          <CardDescription>
            List and fulfil across eBay marketplaces. DropPilot uses its own eBay
            developer application — you only approve access for your seller
            account, and you never enter eBay credentials here.
          </CardDescription>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {isError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription className="flex items-center justify-between gap-3">
              <span>Could not load the connection status.</span>
              <Button variant="outline" size="sm" onClick={() => void refetch()}>
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {!isPending && !isError && !configured && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              eBay is not configured on this server. Ask your DropPilot operator
              to add the eBay application credentials before connecting.
            </AlertDescription>
          </Alert>
        )}

        {actionError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{actionError}</AlertDescription>
          </Alert>
        )}

        {connection?.needsReconnect && (
          <Alert variant="destructive" data-testid="ebay-reconnect-notice">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              {(connection.reconnectReason &&
                RECONNECT_REASONS[connection.reconnectReason]) ??
                "The eBay authorization is no longer valid. Reconnect to restore it."}
            </AlertDescription>
          </Alert>
        )}

        {connection?.lastError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{connection.lastError}</AlertDescription>
          </Alert>
        )}

        {isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-4 w-40" />
          </div>
        ) : connection ? (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-foreground">Seller</dt>
              <dd data-testid="ebay-username">
                {connection.ebayUsername ?? "Unknown"}
              </dd>
            </div>
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-foreground">Marketplace</dt>
              <dd>{marketplaceLabel(connection.marketplaceId)}</dd>
            </div>
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-foreground">Connected</dt>
              <dd>{formatDate(connection.connectedAt)}</dd>
            </div>
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-foreground">Last verified</dt>
              <dd>{formatDate(connection.lastVerifiedAt)}</dd>
            </div>
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">
            Click Connect to authorize DropPilot on eBay. You will sign in with
            your eBay seller account and approve the access DropPilot needs to
            manage listings and orders.
          </p>
        )}
      </CardContent>

      <CardFooter className="gap-2">
        {connection ? (
          <>
            <Button
              variant={connection.needsReconnect ? "default" : "outline"}
              onClick={() => void handleConnect()}
              disabled={busy || !configured}
            >
              {connect.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Link2 className="h-4 w-4" />
              )}
              Reconnect
            </Button>
            <Button
              variant="ghost"
              onClick={() => void handleDisconnect()}
              disabled={busy}
            >
              {disconnect.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Unlink className="h-4 w-4" />
              )}
              Disconnect
            </Button>
          </>
        ) : (
          <Button
            onClick={() => void handleConnect()}
            disabled={isPending || busy || !configured}
          >
            {connect.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Link2 className="h-4 w-4" />
            )}
            {connect.isPending ? "Connecting..." : "Connect eBay"}
          </Button>
        )}
      </CardFooter>
    </Card>
  );
}
