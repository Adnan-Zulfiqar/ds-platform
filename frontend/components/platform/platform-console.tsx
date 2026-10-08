"use client";

import { useState, type FormEvent } from "react";

import {
  PlatformMySessions,
  PlatformOperators,
} from "@/components/platform/platform-operators";
import {
  ReauthProvider,
  useReauthGuard,
} from "@/components/platform/reauth-dialog";
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
import { cn, formatDateTime } from "@/lib/utils";
import {
  type PlatformMe,
  type PlatformPermission,
  type PlatformTenant,
  roleLabel,
  usePlatformAudit,
  usePlatformLogin,
  usePlatformLogout,
  usePlatformMe,
  usePlatformSignedIn,
  usePlatformTenantHealth,
  usePlatformTenants,
  useSetTenantActive,
} from "@/services/platform";

/**
 * Track E5d / D-018: the platform-operator console. Not part of the tenant
 * app: no tenant session, no tenant navigation. On a deployment where the
 * panel is switched off the API answers 404, and the sign-in form says so.
 *
 * Tabs and buttons follow the operator's permissions from `/me`. That is a
 * convenience only: every route checks the same permission on the server.
 */
export function PlatformConsole() {
  const signedIn = usePlatformSignedIn();
  return (
    <main className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8" id="main-content">
      {signedIn ? (
        <ReauthProvider>
          <SignedIn />
        </ReauthProvider>
      ) : (
        <>
          <Title />
          <SignIn />
        </>
      )}
    </main>
  );
}

function Title({ children }: { children?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold">DropPilot platform</h1>
        <p className="text-sm text-muted-foreground">
          Operator console. Every action is audited.
        </p>
      </div>
      {children}
    </header>
  );
}

type Tab = "workspaces" | "operators" | "audit" | "sessions";

const TABS: { id: Tab; label: string; needs: PlatformPermission | null }[] = [
  { id: "workspaces", label: "Workspaces", needs: "tenants.read" },
  { id: "operators", label: "Operators", needs: "operators.read" },
  { id: "audit", label: "Audit log", needs: "audit.read" },
  { id: "sessions", label: "My sessions", needs: null },
];

function can(me: PlatformMe, permission: PlatformPermission): boolean {
  return me.permissions.includes(permission);
}

function SignedIn() {
  const me = usePlatformMe();
  const logout = usePlatformLogout();
  const [tab, setTab] = useState<Tab | null>(null);

  if (me.isError) {
    return (
      <>
        <Title />
        <ErrorState
          title="Could not load your operator account"
          onRetry={() => void me.refetch()}
        />
      </>
    );
  }
  if (!me.data) {
    return (
      <>
        <Title />
        <Skeleton className="h-64 w-full" />
      </>
    );
  }
  const visible = TABS.filter((t) => t.needs === null || can(me.data, t.needs));
  const active =
    tab && visible.some((t) => t.id === tab)
      ? tab
      : (visible[0]?.id ?? "sessions");

  return (
    <>
      <Title>
        <div className="flex items-center gap-3 text-sm">
          <span
            className="text-muted-foreground"
            data-testid="platform-operator-email"
          >
            {me.data.email}
          </span>
          <Badge variant="outline" data-testid="platform-operator-role">
            {roleLabel(me.data.role)}
          </Badge>
          <Button
            variant="outline"
            disabled={logout.isPending}
            onClick={() => logout.mutate()}
          >
            Sign out
          </Button>
        </div>
      </Title>
      <nav
        aria-label="Console sections"
        className="flex flex-wrap gap-1 border-b"
      >
        {visible.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-current={active === t.id ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm",
              active === t.id
                ? "border-primary font-medium"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {active === "workspaces" && (
        <Workspaces canSuspend={can(me.data, "tenants.suspend")} />
      )}
      {active === "operators" && (
        <PlatformOperators
          selfId={me.data.id}
          canManage={can(me.data, "operators.manage")}
        />
      )}
      {active === "audit" && <Audit />}
      {active === "sessions" && <PlatformMySessions />}
    </>
  );
}

function SignIn() {
  const login = usePlatformLogin();
  const [form, setForm] = useState({ email: "", password: "", code: "" });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    login.mutate(form, {
      onSettled: () => setForm((f) => ({ ...f, password: "", code: "" })),
    });
  }

  const error =
    login.error instanceof ApiError && login.error.status === 404
      ? "The platform console is not enabled for this network."
      : login.error
        ? "Sign-in failed. Check the email, password and one-time code."
        : null;

  return (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle className="text-base">Operator sign-in</CardTitle>
        <CardDescription>
          Password and the 6-digit code from your authenticator app.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-3" onSubmit={onSubmit} noValidate>
          <div className="space-y-1">
            <Label htmlFor="platform-email">Email</Label>
            <Input
              id="platform-email"
              type="email"
              autoComplete="username"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="platform-password">Password</Label>
            <Input
              id="platform-password"
              type="password"
              autoComplete="current-password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="platform-code">One-time code</Label>
            <Input
              id="platform-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={form.code}
              onChange={(e) =>
                setForm({ ...form, code: e.target.value.replace(/\D/g, "") })
              }
            />
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button
            type="submit"
            disabled={
              login.isPending ||
              !form.email ||
              !form.password ||
              form.code.length !== 6
            }
          >
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function Workspaces({ canSuspend }: { canSuspend: boolean }) {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<PlatformTenant | null>(null);
  const tenants = usePlatformTenants(submitted, page, true);

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
      <div className="space-y-4">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            setSubmitted(query.trim());
          }}
        >
          <Input
            aria-label="Search workspaces"
            placeholder="Search by name or slug"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button type="submit" variant="outline">
            Search
          </Button>
        </form>
        {tenants.isError ? (
          <ErrorState
            title="Could not load workspaces"
            onRetry={() => void tenants.refetch()}
          />
        ) : !tenants.data ? (
          <Skeleton className="h-64 w-full" />
        ) : tenants.data.items.length === 0 ? (
          <EmptyState
            title="No workspaces"
            description={
              submitted
                ? "Nothing matches that search."
                : "No workspace exists yet."
            }
          />
        ) : (
          <>
            <ul
              className="divide-y rounded-lg border"
              data-testid="platform-tenants"
            >
              {tenants.data.items.map((tenant) => (
                <li key={tenant.id}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-3 p-3 text-left text-sm hover:bg-accent/40"
                    onClick={() => setSelected(tenant)}
                    aria-pressed={tenant.id === selected?.id}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">
                        {tenant.name}
                      </span>
                      <span className="block truncate text-muted-foreground">
                        {tenant.slug} · {tenant.users} users ·{" "}
                        {tenant.connectedStores} connected stores
                      </span>
                    </span>
                    <Badge
                      variant={tenant.isActive ? "outline" : "destructive"}
                    >
                      {tenant.status}
                    </Badge>
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Button
                size="sm"
                variant="outline"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Previous
              </Button>
              <span>
                Page {page} of {Math.max(1, tenants.data.meta.totalPages)}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={!tenants.data.meta.hasNext}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </div>
      {selected && (
        <WorkspacePanel
          key={selected.id}
          tenant={selected}
          canSuspend={canSuspend}
          onChanged={setSelected}
        />
      )}
    </div>
  );
}

function WorkspacePanel({
  tenant,
  canSuspend,
  onChanged,
}: {
  tenant: PlatformTenant;
  canSuspend: boolean;
  onChanged: (tenant: PlatformTenant) => void;
}) {
  const health = usePlatformTenantHealth(tenant.id);
  const change = useSetTenantActive();
  const guard = useReauthGuard();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const nextActive = !tenant.isActive;

  function submit() {
    setError(null);
    guard(() =>
      change.mutateAsync({
        tenantId: tenant.id,
        active: nextActive,
        reason: reason.trim(),
      }),
    ).then(
      (updated) => {
        setReason("");
        onChanged(updated);
      },
      (e: unknown) => {
        if (e instanceof ApiError && e.code === "reauth_cancelled") return;
        setError("The change was not made.");
      },
    );
  }

  return (
    <Card data-testid="platform-tenant-panel">
      <CardHeader>
        <CardTitle className="text-base">{tenant.name}</CardTitle>
        <CardDescription>
          {tenant.slug} · created {formatDateTime(tenant.createdAt)}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div>
          <p className="font-medium">
            Last {health.data?.windowHours ?? 24} hours
          </p>
          {health.isError ? (
            <p className="text-muted-foreground">Health counts unavailable.</p>
          ) : health.data ? (
            <ul
              className="text-muted-foreground"
              data-testid="platform-tenant-health"
            >
              <li>Failed order syncs: {health.data.failedOrderSyncs}</li>
              <li>
                Failed inventory syncs: {health.data.failedInventorySyncs}
              </li>
              <li>Listings in error: {health.data.listingsInError}</li>
              <li>
                Failed notification emails:{" "}
                {health.data.failedNotificationEmails}
              </li>
            </ul>
          ) : (
            <Skeleton className="h-16 w-full" />
          )}
        </div>
        {canSuspend && (
          <div className="space-y-2">
            <Label htmlFor="platform-reason">
              Reason (recorded in the audit log)
            </Label>
            <Input
              id="platform-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <Button
              variant={nextActive ? "default" : "destructive"}
              disabled={reason.trim().length < 3 || change.isPending}
              onClick={submit}
            >
              {nextActive ? "Reactivate workspace" : "Suspend workspace"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Audit() {
  const audit = usePlatformAudit(true);
  if (audit.isError) {
    return (
      <ErrorState
        title="Could not load the audit log"
        onRetry={() => void audit.refetch()}
      />
    );
  }
  if (!audit.data) return <Skeleton className="h-64 w-full" />;
  if (audit.data.length === 0) {
    return (
      <EmptyState
        title="No operator actions yet"
        description="Every action appears here."
      />
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Operator actions</CardTitle>
        <CardDescription>
          The latest 100, newest first. Rows cannot be edited or deleted.
        </CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table
          className="w-full text-left text-sm"
          data-testid="platform-audit"
        >
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-3 font-normal">When</th>
              <th className="py-1 pr-3 font-normal">Action</th>
              <th className="py-1 pr-3 font-normal">Outcome</th>
              <th className="py-1 pr-3 font-normal">Role</th>
              <th className="py-1 pr-3 font-normal">Target</th>
              <th className="py-1 pr-3 font-normal">Address</th>
              <th className="py-1 font-normal">Reason</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {audit.data.map((entry) => (
              <tr key={entry.id}>
                <td className="whitespace-nowrap py-1 pr-3 text-muted-foreground">
                  {formatDateTime(entry.createdAt)}
                </td>
                <td className="py-1 pr-3 font-medium">{entry.action}</td>
                <td className="py-1 pr-3">
                  <Badge
                    variant={
                      entry.outcome === "success" ? "outline" : "destructive"
                    }
                  >
                    {entry.outcome}
                  </Badge>
                </td>
                <td className="py-1 pr-3">
                  {entry.actorRole ? roleLabel(entry.actorRole) : "—"}
                </td>
                <td className="py-1 pr-3 text-muted-foreground">
                  {entry.targetType
                    ? `${entry.targetType} ${entry.targetId?.slice(0, 8) ?? ""}`
                    : "—"}
                </td>
                <td className="py-1 pr-3 text-muted-foreground">
                  {entry.clientIp ?? "—"}
                </td>
                <td className="max-w-[16rem] truncate py-1 text-muted-foreground">
                  {typeof entry.detail.reason === "string"
                    ? entry.detail.reason
                    : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
