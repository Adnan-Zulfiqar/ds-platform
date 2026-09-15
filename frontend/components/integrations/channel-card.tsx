import type { ReactNode } from "react";

import { ChannelStatusBadge } from "@/components/integrations/channel-status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ChannelState } from "@/lib/channel-state";
import { cn } from "@/lib/utils";

interface ChannelCardProps {
  /** Stable id used for test ids: `channel-{id}`. */
  id: string;
  name: string;
  description: string;
  state: ChannelState | null;
  /** True while the status is being fetched for the first time. */
  loading?: boolean;
  /** The merchant cannot change this channel (role). */
  readOnly?: boolean;
  readOnlyHint?: string;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
  "data-testid"?: string;
}

/**
 * The frame every provider shares: name, one status badge, one sentence of
 * description, a body for identity and messages, a footer for actions.
 *
 * Every question the merchant brings to this page is answered in the same
 * place on every card — which provider, is it connected, which account, what
 * is wrong, what do I do — so learning one card is learning all of them, and
 * a future channel is a new body and footer, not a new layout.
 */
export function ChannelCard({
  id,
  name,
  description,
  state,
  loading = false,
  readOnly = false,
  readOnlyHint = "Your role can view this connection. Ask an administrator to make changes.",
  children,
  actions,
  className,
  ...rest
}: ChannelCardProps) {
  return (
    <Card
      id={`channel-${id}`}
      className={cn("scroll-mt-24 overflow-hidden", className)}
      data-testid={rest["data-testid"] ?? `channel-${id}`}
    >
      <CardHeader className="space-y-1.5">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <CardTitle className="text-base">{name}</CardTitle>
          {loading || !state ? (
            <Skeleton className="h-5 w-24 rounded-full" />
          ) : (
            <ChannelStatusBadge state={state} data-testid={`channel-${id}-status`} />
          )}
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!loading && state ? (
          <p className="text-sm text-foreground" id={`channel-${id}-detail`} data-testid={`channel-${id}-detail`}>
            {state.detail}
          </p>
        ) : null}
        {children}
      </CardContent>
      {actions || readOnly ? (
        <CardFooter className="flex flex-wrap items-center gap-2">
          {readOnly ? (
            <p className="text-xs text-muted-foreground" data-testid={`channel-${id}-read-only`}>
              <Badge variant="outline" className="mr-2">
                Read only
              </Badge>
              {readOnlyHint}
            </p>
          ) : (
            actions
          )}
        </CardFooter>
      ) : null}
    </Card>
  );
}

/** A labelled fact about the connected account: store domain, seller, dates. */
export function ChannelFacts({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.label} className="flex min-w-0 justify-between gap-2 sm:block">
          <dt className="shrink-0 text-muted-foreground">{item.label}</dt>
          <dd className="min-w-0 break-words text-right sm:text-left">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function formatChannelDate(value: string | null | undefined): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
