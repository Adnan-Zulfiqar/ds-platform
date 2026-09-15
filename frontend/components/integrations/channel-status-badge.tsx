import { AlertTriangle, CheckCircle2, Loader2, type LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ChannelState, ChannelTone } from "@/lib/channel-state";
import { cn } from "@/lib/utils";

const VARIANT: Record<ChannelTone, "success" | "warning" | "destructive" | "secondary"> = {
  success: "success",
  warning: "warning",
  danger: "destructive",
  neutral: "secondary",
};

const ICON: Partial<Record<ChannelState["kind"], LucideIcon>> = {
  connected: CheckCircle2,
  "needs-attention": AlertTriangle,
  "reconnect-required": AlertTriangle,
  checking: Loader2,
};

interface ChannelStatusBadgeProps {
  state: ChannelState;
  className?: string;
  "data-testid"?: string;
}

/**
 * The one way a channel's state is shown. Text always, colour as a second
 * signal, an icon for the states that ask for a decision — never a dot.
 */
export function ChannelStatusBadge({ state, className, ...rest }: ChannelStatusBadgeProps) {
  const Icon = ICON[state.kind];
  return (
    <Badge
      variant={VARIANT[state.tone]}
      className={cn("gap-1 whitespace-nowrap", className)}
      data-kind={state.kind}
      {...rest}
    >
      {Icon ? (
        <Icon
          className={cn("h-3 w-3", state.kind === "checking" && "animate-spin motion-reduce:animate-none")}
          aria-hidden="true"
        />
      ) : null}
      {state.label}
    </Badge>
  );
}
