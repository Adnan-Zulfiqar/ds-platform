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
  useAliExpressStatus,
  useConnectAliExpress,
  useDisconnectAliExpress,
} from "@/services/integrations";
import type { AliExpressConnection, IntegrationStatus } from "@/types/api";

/**
 * AliExpress connection card.
 *
 * **The status shown is always the server's.** It is never inferred, cached
 * optimistically, or assumed from the fact that a connect flow was started. A
 * dashboard that claims a supplier is connected when it is not would let an
 * operator believe their orders are being fulfilled while nothing is happening.
 *
 * Four states are rendered distinctly — loading, not connected, pending, and
 * connected — plus an error banner drawn from the server's `lastError`.
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

  // The label carries the meaning; the colour only reinforces it. A status
  // conveyed by hue alone is invisible to colour-blind users.
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

  const [dialogOpen, setDialogOpen] = useState(false);
  const [appKey, setAppKey] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const connection = data?.connection ?? null;

  async function handleConnect() {
    setFormError(null);

    const trimmedKey = appKey.trim();
    const trimmedSecret = appSecret.trim();
    const hasPartialCredentials =
      Boolean(trimmedKey) !== Boolean(trimmedSecret);

    if (hasPartialCredentials) {
      setFormError("Supply both the app key and app secret, or leave both blank.");
      return;
    }

    try {
      const authorization = await connect.mutateAsync(
        trimmedKey && trimmedSecret
          ? { appKey: trimmedKey, appSecret: trimmedSecret }
          : {},
      );

      // Clear the secret from component state before leaving the page. It is
      // about to be out of scope anyway, but not holding a credential longer
      // than needed is the habit worth keeping.
      setAppSecret("");
      setDialogOpen(false);

      // A full navigation, not a router push: the destination is AliExpress,
      // outside this application.
      window.location.assign(authorization.authorizationUrl);
    } catch (error) {
      setFormError(
        error instanceof ApiError
          ? error.message
          : "Could not start the connection. Please try again.",
      );
    }
  }

  return (
    <>
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
              Source products and automate order fulfilment through the
              AliExpress Open Platform.
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
                <dt className="text-muted-foreground">App key</dt>
                <dd className="truncate font-mono text-xs">{connection.appKey}</dd>
              </div>
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
              Connect using the platform application configured on the server, or
              supply your own app key and secret from the AliExpress developer
              console.
            </p>
          )}
        </CardContent>

        <CardFooter className="gap-2">
          {connection ? (
            <>
              <Button
                variant="outline"
                onClick={() => setDialogOpen(true)}
                disabled={connect.isPending}
              >
                <Link2 className="h-4 w-4" />
                Reconnect
              </Button>
              <Button
                variant="ghost"
                onClick={() => void disconnect.mutateAsync()}
                disabled={disconnect.isPending}
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
            <Button onClick={() => setDialogOpen(true)} disabled={isPending}>
              <Link2 className="h-4 w-4" />
              Connect
            </Button>
          )}
        </CardFooter>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Connect AliExpress</DialogTitle>
            <DialogDescription>
              Leave the fields blank to use the platform&apos;s AliExpress
              application, or enter your own credentials if you operate a
              separate developer account. You will then be redirected to
              AliExpress to authorise access.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {formError && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{formError}</AlertDescription>
              </Alert>
            )}

            <div className="space-y-2">
              <Label htmlFor="aliexpress-app-key">App key</Label>
              <Input
                id="aliexpress-app-key"
                value={appKey}
                onChange={(event) => setAppKey(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                disabled={connect.isPending}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="aliexpress-app-secret">App secret</Label>
              <Input
                id="aliexpress-app-secret"
                // A password field: masked on screen, and excluded from
                // autofill and password-manager capture by autoComplete="off".
                type="password"
                value={appSecret}
                onChange={(event) => setAppSecret(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                disabled={connect.isPending}
              />
              <p className="text-xs text-muted-foreground">
                Encrypted before storage. It is never shown again and never
                returned by the API.
              </p>
            </div>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              disabled={connect.isPending}
            >
              Cancel
            </Button>
            <Button onClick={() => void handleConnect()} disabled={connect.isPending}>
              {connect.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {connect.isPending ? "Connecting..." : "Continue to AliExpress"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
