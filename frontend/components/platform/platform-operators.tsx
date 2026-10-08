"use client";

import { useState } from "react";

import { useReauthGuard } from "@/components/platform/reauth-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import {
  PLATFORM_ROLES,
  type OperatorAction,
  type OperatorChange,
  type PlatformOperator,
  type PlatformRole,
  roleLabel,
  useChangeOperator,
  usePlatformOperators,
  usePlatformOperatorSessions,
  usePlatformSessions,
  useRevokePlatformSession,
} from "@/services/platform";

/** D-018: operator accounts. Managing them needs `operators.manage` and a
 * recent re-authentication; the server enforces both. */
export function PlatformOperators({
  selfId,
  canManage,
}: {
  selfId: string;
  canManage: boolean;
}) {
  const operators = usePlatformOperators(true);
  const [selected, setSelected] = useState<string | null>(null);

  if (operators.isError) {
    return (
      <ErrorState
        title="Could not load operators"
        onRetry={() => void operators.refetch()}
      />
    );
  }
  if (!operators.data) return <Skeleton className="h-48 w-full" />;
  if (operators.data.length === 0) {
    return (
      <EmptyState
        title="No operators"
        description="Operators are created on the server."
      />
    );
  }
  const current = operators.data.find((o) => o.id === selected) ?? null;

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
      <ul
        className="divide-y rounded-lg border"
        data-testid="platform-operators"
      >
        {operators.data.map((op) => (
          <li key={op.id}>
            <button
              type="button"
              className="flex w-full items-center gap-3 p-3 text-left text-sm hover:bg-accent/40"
              onClick={() => setSelected(op.id)}
              aria-pressed={op.id === selected}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">
                  {op.email}
                  {op.id === selfId && (
                    <span className="text-muted-foreground"> (you)</span>
                  )}
                </span>
                <span className="block truncate text-muted-foreground">
                  Last sign-in{" "}
                  {op.lastLoginAt ? formatDateTime(op.lastLoginAt) : "never"} ·{" "}
                  {op.openSessions} open session
                  {op.openSessions === 1 ? "" : "s"}
                </span>
              </span>
              <Badge variant="outline">{roleLabel(op.role)}</Badge>
              {!op.isActive && <Badge variant="destructive">deactivated</Badge>}
            </button>
          </li>
        ))}
      </ul>
      {current && (
        <OperatorPanel
          key={current.id}
          operator={current}
          isSelf={current.id === selfId}
          canManage={canManage}
        />
      )}
    </div>
  );
}

function OperatorPanel({
  operator,
  isSelf,
  canManage,
}: {
  operator: PlatformOperator;
  isSelf: boolean;
  canManage: boolean;
}) {
  const change = useChangeOperator();
  const guard = useReauthGuard();
  const [reason, setReason] = useState("");
  const [role, setRole] = useState<PlatformRole>(operator.role as PlatformRole);
  const [error, setError] = useState<string | null>(null);
  const ready = reason.trim().length >= 3 && !change.isPending;

  function run(input: OperatorAction) {
    setError(null);
    const full: OperatorChange = {
      ...input,
      operatorId: operator.id,
      reason: reason.trim(),
    };
    guard(() => change.mutateAsync(full)).then(
      () => setReason(""),
      (e: unknown) => {
        if (e instanceof ApiError && e.code === "reauth_cancelled") return;
        setError(
          e instanceof ApiError ? e.message : "The change was not made.",
        );
      },
    );
  }

  return (
    <Card data-testid="platform-operator-panel">
      <CardHeader>
        <CardTitle className="text-base">{operator.email}</CardTitle>
        <CardDescription>
          {roleLabel(operator.role)} · created{" "}
          {formatDateTime(operator.createdAt)}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <OperatorSessions operatorId={operator.id} />
        {!canManage ? (
          <p className="text-muted-foreground">
            Your role can view operators but not change them.
          </p>
        ) : isSelf ? (
          <p className="text-muted-foreground">
            You cannot change your own role or deactivate yourself. Another
            super admin must.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="platform-operator-reason">
                Reason (recorded in the audit log)
              </Label>
              <Input
                id="platform-operator-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </div>
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1">
                <Label htmlFor="platform-operator-role">Role</Label>
                <select
                  id="platform-operator-role"
                  className="h-9 w-full rounded-md border bg-background px-2"
                  value={role}
                  onChange={(e) => setRole(e.target.value as PlatformRole)}
                >
                  {PLATFORM_ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                variant="outline"
                disabled={!ready || role === operator.role}
                onClick={() => run({ kind: "role", role })}
              >
                Change role
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={!ready || operator.openSessions === 0}
                onClick={() => run({ kind: "revoke-sessions" })}
              >
                End all sessions
              </Button>
              <Button
                variant={operator.isActive ? "destructive" : "default"}
                disabled={!ready}
                onClick={() =>
                  run({ kind: operator.isActive ? "deactivate" : "reactivate" })
                }
              >
                {operator.isActive
                  ? "Deactivate operator"
                  : "Reactivate operator"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              A role change or deactivation ends their sessions at once.
            </p>
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function OperatorSessions({ operatorId }: { operatorId: string }) {
  const sessions = usePlatformOperatorSessions(operatorId);
  if (sessions.isError)
    return <p className="text-muted-foreground">Sessions unavailable.</p>;
  if (!sessions.data) return <Skeleton className="h-10 w-full" />;
  if (sessions.data.length === 0)
    return <p className="text-muted-foreground">No open sessions.</p>;
  return (
    <ul
      className="space-y-1 text-muted-foreground"
      data-testid="platform-operator-sessions"
    >
      {sessions.data.map((s) => (
        <li key={s.id}>
          {s.clientIp ?? "unknown address"} · since{" "}
          {formatDateTime(s.createdAt)}
        </li>
      ))}
    </ul>
  );
}

/** The operator's own sessions, with "end" for every one but this. */
export function PlatformMySessions() {
  const sessions = usePlatformSessions();
  const revoke = useRevokePlatformSession();

  if (sessions.isError) {
    return (
      <ErrorState
        title="Could not load sessions"
        onRetry={() => void sessions.refetch()}
      />
    );
  }
  if (!sessions.data) return <Skeleton className="h-32 w-full" />;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Your sessions</CardTitle>
        <CardDescription>
          Each sign-in is a session. End any you do not recognise, then change
          your password.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y text-sm" data-testid="platform-my-sessions">
          {sessions.data.map((s) => (
            <li key={s.id} className="flex items-center gap-3 py-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">
                  {s.clientIp ?? "unknown address"}
                  {s.current && (
                    <span className="text-muted-foreground">
                      {" "}
                      (this session)
                    </span>
                  )}
                </span>
                <span className="block truncate text-muted-foreground">
                  {s.userAgent ?? "unknown browser"} · signed in{" "}
                  {formatDateTime(s.createdAt)} · expires{" "}
                  {formatDateTime(s.expiresAt)}
                </span>
              </span>
              {!s.current && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(s.id)}
                >
                  End session
                </Button>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
