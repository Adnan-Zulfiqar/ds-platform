"use client";

import { LifeBuoy } from "lucide-react";
import { useState, type ReactNode } from "react";

import { usePlatformAccess } from "@/components/platform/platform-shell";
import { useReauthGuard } from "@/components/platform/reauth-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import {
  useEndSupportSession,
  useOpenSupportSession,
  useSupportSession,
} from "@/services/platform";

/**
 * Admin Control Center phase 4 (D-019): changes inside a workspace.
 *
 * The server needs, for every change: the permission, a fresh
 * re-authentication, and an open support session for this workspace. The
 * console asks for re-authentication when told to, and explains a missing
 * support session instead of failing silently.
 */

export function describeError(e: unknown): string | null {
  if (e instanceof ApiError) {
    if (e.code === "reauth_cancelled") return null;
    if (e.code === "support_session_required") {
      return "Open a support session for this workspace first (top of the page).";
    }
    if (e.code === "permission_denied")
      return "Your role cannot make this change.";
    return e.message;
  }
  return "The change was not made.";
}

/**
 * A change with a required reason. The button stays disabled until the
 * reason is long enough; a destructive change is styled as one, and asks
 * for a second click to confirm.
 */
export function ReasonedAction({
  label,
  description,
  destructive = false,
  disabled = false,
  onRun,
  testId,
  id: idProp,
}: {
  label: string;
  description?: ReactNode;
  destructive?: boolean;
  disabled?: boolean;
  onRun: (reason: string) => Promise<unknown>;
  testId?: string;
  /** Unique per page when the same label appears more than once. */
  id?: string;
}) {
  const guard = useReauthGuard();
  const [reason, setReason] = useState("");
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const id = idProp ?? `reason-${label.toLowerCase().replaceAll(/\W+/g, "-")}`;
  const ready = reason.trim().length >= 3 && !busy && !disabled;

  function run() {
    if (destructive && !armed) {
      setArmed(true);
      return;
    }
    setError(null);
    setBusy(true);
    guard(() => onRun(reason.trim()))
      .then(() => {
        setReason("");
        setDone(true);
      })
      .catch((e: unknown) => setError(describeError(e)))
      .finally(() => {
        setBusy(false);
        setArmed(false);
      });
  }

  return (
    <div className="space-y-2 rounded-md border p-3" data-testid={testId}>
      <div>
        <p className="text-sm font-medium">{label}</p>
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      <Label htmlFor={id} className="sr-only">
        Reason for {label}
      </Label>
      <Input
        id={id}
        placeholder="Reason (recorded in the audit log)"
        value={reason}
        onChange={(e) => {
          setReason(e.target.value);
          setDone(false);
          setArmed(false);
        }}
      />
      <Button
        size="sm"
        variant={destructive ? "destructive" : "outline"}
        disabled={!ready}
        onClick={run}
      >
        {busy ? "Working…" : armed ? `Confirm: ${label.toLowerCase()}` : label}
      </Button>
      {done && (
        <p className="text-xs text-muted-foreground">
          Done. Recorded in the audit log.
        </p>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}

/** The operator's support session for this workspace: open, extend or end. */
export function SupportSessionBanner({ tenantId }: { tenantId: string }) {
  const { can } = usePlatformAccess();
  const current = useSupportSession(tenantId);
  const open = useOpenSupportSession(tenantId);
  const end = useEndSupportSession(tenantId);
  const guard = useReauthGuard();
  const [reason, setReason] = useState("");
  const [minutes, setMinutes] = useState(30);
  const [error, setError] = useState<string | null>(null);

  if (!can("support.session")) return null;
  const session = current.data;

  function start() {
    setError(null);
    guard(() => open.mutateAsync({ reason: reason.trim(), minutes })).then(
      () => setReason(""),
      (e: unknown) => setError(describeError(e)),
    );
  }

  return (
    <Card data-testid="platform-support-session">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <LifeBuoy className="h-4 w-4" aria-hidden />
          Support session
        </CardTitle>
        <CardDescription>
          {session
            ? `Open until ${formatDateTime(session.expiresAt)}. Reason: ${session.reason}`
            : "Needed for any change in this workspace. The workspace sees a notice that support is working in it."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-end gap-2">
        {session ? (
          <Button
            size="sm"
            variant="outline"
            disabled={end.isPending}
            onClick={() => end.mutate()}
          >
            End support session
          </Button>
        ) : (
          <>
            <div className="min-w-[16rem] flex-1 space-y-1">
              <Label htmlFor="support-reason">
                Reason (shown to the workspace)
              </Label>
              <Input
                id="support-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="support-minutes">Minutes</Label>
              <select
                id="support-minutes"
                className="h-9 rounded-md border bg-background px-2 text-sm"
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
              >
                {[15, 30, 60, 120].map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
            <Button
              size="sm"
              disabled={reason.trim().length < 3 || open.isPending}
              onClick={start}
            >
              Open support session
            </Button>
          </>
        )}
        {error && (
          <Alert variant="destructive" className="w-full">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}
