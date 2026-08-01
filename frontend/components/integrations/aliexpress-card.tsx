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
  useAliExpressStatus,
  useConnectAliExpress,
  useDisconnectAliExpress,
} from "@/services/integrations";
import type { AliExpressConnection, IntegrationStatus } from "@/types/api";

/**
 * AliExpress connection card.
 *
 * Merchants authorize DropPilot's platform AliExpress application. They never
 * enter an app key or secret — those live in server environment configuration.
 *
 * Status is always the server's. It is never inferred optimistically from the
 * fact that a connect flow was started.
 */

const STATUS_LABELS: Record<IntegrationStatus, string> = {
  pending: "Awaiting authorization",
  connected: "Connected",
  expired: "Reconnection required",
  error: "Connection error",
};

function StatusBadge({ connection }: { connection: AliExpressConnection | null }) {
  if (!connection) {
    return <Badge variant="secondary">Not connected</Badge>;
  }

  const variant =
    connection.status === "connected" && !connection.isTokenExpired
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

export function AliExpressCard() {
  const { data, isPending, isError, refetch } = useAliExpressStatus();
  const connect = useConnectAliExpress();
  const disconnect = useDisconnectAliExpress();
  const [actionError, setActionError] = useState<string | null>(null);

  const connection = data?.connection ?? null;

  async function handleConnect() {
    setActionError(null);
    try {
      const authorization = await connect.mutateAsync();
      // Full navigation: destination is AliExpress, outside this application.
      window.location.assign(authorization.authorizationUrl);
    } catch (error) {
      setActionError(
        error instanceof ApiError
          ? error.message
          : "Could not start the connection. Please try again.",
      );
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0 gap-4">
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-base">AliExpress</CardTitle>
            {isPending ? (
              <Skeleton className="h-5 w-24 rounded-full" />
            ) : (
              <StatusBadge connection={connection} />
            )}
          </div>
          <CardDescription>
            Source products and automate order fulfilment through the AliExpress
            Open Platform. DropPilot uses its own developer application — you only
            approve access for your seller account.
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

        {actionError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{actionError}</AlertDescription>
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
              <dt className="text-muted-foreground">Connected</dt>
              <dd>{formatDate(connection.connectedAt)}</dd>
            </div>
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-foreground">Last sync</dt>
              <dd>{formatDate(connection.lastSyncAt)}</dd>
            </div>
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-foreground">Token expires</dt>
              <dd>{formatDate(connection.tokenExpiresAt)}</dd>
            </div>
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">
            Click Connect to authorize DropPilot on AliExpress. You will sign in
            with your AliExpress seller account — no developer credentials needed.
          </p>
        )}
      </CardContent>

      <CardFooter className="gap-2">
        {connection ? (
          <>
            <Button
              variant="outline"
              onClick={() => void handleConnect()}
              disabled={connect.isPending || disconnect.isPending}
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
              onClick={() => void disconnect.mutateAsync()}
              disabled={disconnect.isPending || connect.isPending}
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
            disabled={isPending || connect.isPending}
          >
            {connect.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Link2 className="h-4 w-4" />
            )}
            {connect.isPending ? "Connecting..." : "Connect AliExpress"}
          </Button>
        )}
      </CardFooter>
    </Card>
  );
}
