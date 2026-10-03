"use client";

import { AlertCircle, Link2, Loader2, RefreshCw, Unlink } from "lucide-react";
import { useState } from "react";

import { ChannelCard, ChannelFacts, formatChannelDate } from "@/components/integrations/channel-card";
import { DisconnectDialog } from "@/components/integrations/disconnect-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { deriveAliExpressChannel } from "@/lib/channel-state";
import { useAuth } from "@/providers/auth-provider";
import {
  useAliExpressStatus,
  useConnectAliExpress,
  useDisconnectAliExpress,
} from "@/services/integrations";

/**
 * AliExpress supplier connection.
 *
 * Merchants authorize DropPilot's platform AliExpress application. They never
 * enter an app key or secret — those live in server environment configuration.
 *
 * Status is always the server's. It is never inferred from the fact that a
 * connect flow was started. The status endpoint cannot say whether the server
 * has app credentials at all, so "Setup unavailable" is only known once a
 * connect attempt is refused for that reason — and from then on the card says
 * so instead of offering a button that would fail again.
 */

/** The backend's own phrasing for a missing platform app (a 422 on connect). */
const NOT_CONFIGURED = /not configured on this server/i;

const DISCONNECT_CONSEQUENCES = [
  "The stored AliExpress authorization for this workspace is deleted.",
  "Order placement and tracking sync through AliExpress stop until you reconnect.",
  "Product import and drafts already in DropPilot are kept — import uses the platform catalog.",
];

export function AliExpressCard() {
  const { hasRole } = useAuth();
  const canManage = hasRole("owner") || hasRole("admin");
  const statusQuery = useAliExpressStatus();
  const connect = useConnectAliExpress();
  const disconnect = useDisconnectAliExpress();
  const [setupUnavailable, setSetupUnavailable] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);

  const channel = deriveAliExpressChannel(statusQuery, { setupUnavailable });
  const connection = statusQuery.data?.connection ?? null;
  const loading = channel.kind === "checking";
  const busy = connect.isPending || disconnect.isPending;

  async function handleConnect() {
    setActionError(null);
    try {
      const authorization = await connect.mutateAsync();
      // Full navigation: destination is AliExpress, outside this application.
      window.location.assign(authorization.authorizationUrl);
    } catch (error) {
      if (error instanceof ApiError && error.status === 422 && NOT_CONFIGURED.test(error.message)) {
        // The server named its environment variables; the merchant cannot act
        // on them, so the card switches to the operator-facing state instead.
        setSetupUnavailable(true);
        return;
      }
      setActionError(
        error instanceof ApiError ? error.message : "Could not start the connection. Please try again.",
      );
    }
  }

  async function confirmDisconnect() {
    setDisconnectError(null);
    try {
      await disconnect.mutateAsync();
      setDisconnectOpen(false);
    } catch (error) {
      setDisconnectError(
        error instanceof ApiError ? error.message : "Could not disconnect. Please try again.",
      );
    }
  }

  const connectLabel =
    channel.actions.includes("continue")
      ? "Continue on AliExpress"
      : channel.actions.includes("reconnect")
        ? "Reconnect"
        : "Connect AliExpress";
  const connectPrimary = channel.kind !== "connected";

  return (
    <>
      <ChannelCard
        id="aliexpress"
        name="AliExpress"
        description="Source products and automate order fulfilment through the AliExpress Open Platform. DropPilot uses its own developer application — you only approve access for your seller account."
        state={channel}
        loading={loading}
        readOnly={!loading && !canManage && channel.kind !== "unavailable"}
        readOnlyHint="Your role can view this connection. Ask an administrator to make changes."
        actions={
          channel.kind === "unavailable" ? (
            <Button variant="outline" className="min-h-11 sm:min-h-9" onClick={() => void statusQuery.refetch()}>
              <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
              Try again
            </Button>
          ) : channel.actions.length > 0 ? (
            <>
              <Button
                variant={connectPrimary ? "default" : "outline"}
                className="min-h-11 sm:min-h-9"
                onClick={() => void handleConnect()}
                disabled={busy}
              >
                {connect.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                ) : (
                  <Link2 className="mr-2 h-4 w-4" aria-hidden="true" />
                )}
                {connect.isPending ? "Connecting…" : connectLabel}
              </Button>
              {channel.actions.includes("disconnect") ? (
                <Button
                  variant="ghost"
                  className="min-h-11 sm:min-h-9"
                  onClick={() => {
                    setDisconnectError(null);
                    setDisconnectOpen(true);
                  }}
                  disabled={busy}
                >
                  <Unlink className="mr-2 h-4 w-4" aria-hidden="true" />
                  Disconnect
                </Button>
              ) : null}
            </>
          ) : undefined
        }
      >
        {channel.kind === "unavailable" ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>Could not load the connection status.</AlertDescription>
          </Alert>
        ) : null}

        {actionError ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{actionError}</AlertDescription>
          </Alert>
        ) : null}

        {/* The backend's curated explanation (its exception catalogue, never
            upstream text), shown only in the states where it adds something. */}
        {channel.message ? (
          <Alert variant={channel.kind === "reconnect-required" ? "destructive" : "warning"}>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{channel.message}</AlertDescription>
          </Alert>
        ) : null}

        {connection && channel.kind !== "setup-unavailable" ? (
          <ChannelFacts
            items={[
              { label: "Connected", value: formatChannelDate(connection.connectedAt) },
              { label: "Last sync", value: formatChannelDate(connection.lastSyncAt) },
            ]}
          />
        ) : null}
      </ChannelCard>

      <DisconnectDialog
        id="aliexpress"
        open={disconnectOpen}
        onOpenChange={setDisconnectOpen}
        provider="AliExpress"
        consequences={DISCONNECT_CONSEQUENCES}
        pending={disconnect.isPending}
        error={disconnectError}
        onConfirm={() => void confirmDisconnect()}
      />
    </>
  );
}
