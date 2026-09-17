"use client";

import { AlertCircle, Link2, Loader2, RefreshCw, Unlink } from "lucide-react";
import { useState } from "react";

import { ChannelCard, ChannelFacts, formatChannelDate } from "@/components/integrations/channel-card";
import { DisconnectDialog } from "@/components/integrations/disconnect-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { deriveEbayChannel } from "@/lib/channel-state";
import { useAuth } from "@/providers/auth-provider";
import { useConnectEbay, useDisconnectEbay, useEbayStatus } from "@/services/integrations";

/**
 * eBay seller connection.
 *
 * Merchants authorize DropPilot's own eBay application against their seller
 * account. They never enter a client id, certificate id or RuName — those live
 * in server environment configuration, and a field asking for them would
 * invite a merchant to paste a credential into a form.
 *
 * Status is always the server's: `connected` is computed there, because a
 * connection awaiting reconnection is *not* connected.
 */

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

const DISCONNECT_CONSEQUENCES = [
  "The stored eBay authorization for this workspace is deleted.",
  "Listing and order sync through eBay stop until you reconnect.",
  "Listings already on eBay stay on eBay — nothing is removed from your seller account.",
];

export function EbayCard() {
  const { hasRole } = useAuth();
  const canManage = hasRole("owner") || hasRole("admin");
  const statusQuery = useEbayStatus();
  const connect = useConnectEbay();
  const disconnect = useDisconnectEbay();
  const [actionError, setActionError] = useState<string | null>(null);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);

  const channel = deriveEbayChannel(statusQuery);
  const connection = statusQuery.data?.connection ?? null;
  const loading = channel.kind === "checking";
  const busy = connect.isPending || disconnect.isPending;

  async function handleConnect() {
    setActionError(null);
    try {
      const authorization = await connect.mutateAsync();
      // Full navigation: the destination is eBay, outside this application.
      window.location.assign(authorization.authorizationUrl);
    } catch (error) {
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
      setDisconnectError(error instanceof ApiError ? error.message : "Could not disconnect. Please try again.");
    }
  }

  const connectLabel = channel.actions.includes("continue")
    ? "Continue on eBay"
    : channel.actions.includes("reconnect")
      ? "Reconnect"
      : "Connect eBay";
  const connectPrimary = channel.kind !== "connected";

  return (
    <>
      <ChannelCard
        id="ebay"
        data-testid="ebay-card"
        name="eBay"
        description="List and fulfil across eBay marketplaces. DropPilot uses its own eBay developer application — you only approve access for your seller account, and you never enter eBay credentials here."
        state={channel}
        loading={loading}
        readOnly={!loading && !canManage && channel.kind !== "unavailable" && channel.kind !== "setup-unavailable"}
        actions={
          channel.kind === "unavailable" ? (
            <Button variant="outline" className="min-h-11 sm:min-h-9" onClick={() => void statusQuery.refetch()}>
              <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
              Try again
            </Button>
          ) : channel.kind === "setup-unavailable" ? (
            // Kept, disabled and described, rather than removed: the existing
            // eBay checks read this button's disabled state as the signal that
            // the server has no application credentials.
            <Button className="min-h-11 sm:min-h-9" disabled aria-describedby="channel-ebay-detail">
              <Link2 className="mr-2 h-4 w-4" aria-hidden="true" />
              Connect eBay
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

        {channel.reason ? (
          <Alert variant="destructive" data-testid="ebay-reconnect-notice">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{channel.reason}</AlertDescription>
          </Alert>
        ) : null}

        {connection && channel.kind !== "setup-unavailable" ? (
          <ChannelFacts
            items={[
              { label: "Seller", value: <span data-testid="ebay-username">{connection.ebayUsername ?? "Unknown"}</span> },
              { label: "Marketplace", value: marketplaceLabel(connection.marketplaceId) },
              { label: "Connected", value: formatChannelDate(connection.connectedAt) },
              { label: "Last verified", value: formatChannelDate(connection.lastVerifiedAt) },
            ]}
          />
        ) : null}
      </ChannelCard>

      <DisconnectDialog
        id="ebay"
        open={disconnectOpen}
        onOpenChange={setDisconnectOpen}
        provider="eBay"
        identity={connection?.ebayUsername ?? null}
        consequences={DISCONNECT_CONSEQUENCES}
        pending={disconnect.isPending}
        error={disconnectError}
        onConfirm={() => void confirmDisconnect()}
      />
    </>
  );
}
