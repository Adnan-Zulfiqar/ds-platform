"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, MinusCircle, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

import type { ChannelState, ChannelSummary } from "./home-rules";

interface ChannelStatusProps {
  channels: ChannelSummary[];
}

const STATE: Record<ChannelState, { label: string; icon: LucideIcon; className: string }> = {
  connected: { label: "Connected", icon: CheckCircle2, className: "text-success" },
  "needs-attention": { label: "Needs attention", icon: AlertTriangle, className: "text-warning" },
  "not-connected": { label: "Not connected", icon: CircleDashed, className: "text-muted-foreground" },
  "not-configured": { label: "Not configured", icon: MinusCircle, className: "text-muted-foreground" },
};

/**
 * "Are my channels healthy?" — one row per channel the backend reports on.
 *
 * State is an icon plus a word plus a sentence, never a coloured dot. Rows
 * link to Integrations, which is the page that can connect or repair; no
 * credential or token ever appears here (the status endpoints do not return
 * any, and this component renders only the derived summary).
 */
export function ChannelStatus({ channels }: ChannelStatusProps) {
  return (
    <ul className="divide-y rounded-md border bg-card" data-testid="channel-status">
      {channels.map((channel) => {
        const state = STATE[channel.state];
        const Icon = state.icon;
        return (
          <li
            key={channel.id}
            className="flex items-start gap-3 px-4 py-3"
            data-testid={`channel-${channel.id}`}
            data-state={channel.state}
          >
            <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", state.className)} aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">
                {channel.label}
                <span className="text-muted-foreground"> · {state.label}</span>
              </p>
              <p className="text-xs text-muted-foreground">{channel.detail}</p>
            </div>
            <Link
              href="/settings/integrations"
              className="shrink-0 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              aria-label={`Manage ${channel.label}`}
            >
              Manage
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
