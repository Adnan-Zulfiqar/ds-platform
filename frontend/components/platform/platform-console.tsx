"use client";

import { useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import {
  type PlatformTenant,
  signOutPlatform,
  usePlatformAudit,
  usePlatformLogin,
  usePlatformSignedIn,
  usePlatformTenantHealth,
  usePlatformTenants,
  useSetTenantActive,
} from "@/services/platform";

/**
 * Track E5d: the platform-operator console (D-015). Not part of the tenant
 * app: no tenant session, no tenant navigation. On a deployment where the
 * panel is switched off the API answers 404, and the sign-in form says so.
 */
export function PlatformConsole() {
  const signedIn = usePlatformSignedIn();
  return (
    <main className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8" id="main-content">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">DropPilot platform</h1>
          <p className="text-sm text-muted-foreground">Operator console. Every action is audited.</p>
        </div>
        {signedIn && (
          <Button variant="outline" onClick={() => signOutPlatform()}>
            Sign out
          </Button>
        )}
      </header>
      {signedIn ? <Workspaces /> : <SignIn />}
    </main>
  );
}

function SignIn() {
  const login = usePlatformLogin();
  const [form, setForm] = useState({ email: "", password: "", code: "" });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    login.mutate(form, { onSettled: () => setForm((f) => ({ ...f, password: "", code: "" })) });
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
        <CardDescription>Password and the 6-digit code from your authenticator app.</CardDescription>
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
              onChange={(e) => setForm({ ...form, code: e.target.value.replace(/\D/g, "") })}
            />
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button
            type="submit"
            disabled={login.isPending || !form.email || !form.password || form.code.length !== 6}
          >
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function Workspaces() {
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
          <ErrorState title="Could not load workspaces" onRetry={() => void tenants.refetch()} />
        ) : !tenants.data ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <>
            <ul className="divide-y rounded-lg border" data-testid="platform-tenants">
              {tenants.data.items.map((tenant) => (
                <li key={tenant.id}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-3 p-3 text-left text-sm hover:bg-accent/40"
                    onClick={() => setSelected(tenant)}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{tenant.name}</span>
                      <span className="block truncate text-muted-foreground">
                        {tenant.slug} · {tenant.users} users · {tenant.connectedStores} connected
                        stores
                      </span>
                    </span>
                    <Badge variant={tenant.isActive ? "outline" : "destructive"}>{tenant.status}</Badge>
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
        <Audit />
      </div>
      {selected && <WorkspacePanel key={selected.id} tenant={selected} onChanged={setSelected} />}
    </div>
  );
}

function WorkspacePanel({
  tenant,
  onChanged,
}: {
  tenant: PlatformTenant;
  onChanged: (tenant: PlatformTenant) => void;
}) {
  const health = usePlatformTenantHealth(tenant.id);
  const change = useSetTenantActive();
  const [reason, setReason] = useState("");
  const nextActive = !tenant.isActive;

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
          <p className="font-medium">Last {health.data?.windowHours ?? 24} hours</p>
          {health.data ? (
            <ul className="text-muted-foreground" data-testid="platform-tenant-health">
              <li>Failed order syncs: {health.data.failedOrderSyncs}</li>
              <li>Failed inventory syncs: {health.data.failedInventorySyncs}</li>
              <li>Listings in error: {health.data.listingsInError}</li>
              <li>Failed notification emails: {health.data.failedNotificationEmails}</li>
            </ul>
          ) : (
            <Skeleton className="h-16 w-full" />
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="platform-reason">Reason (recorded in the audit log)</Label>
          <Input id="platform-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          {change.isError && (
            <Alert variant="destructive">
              <AlertDescription>The change was not made.</AlertDescription>
            </Alert>
          )}
          <Button
            variant={nextActive ? "default" : "destructive"}
            disabled={reason.trim().length < 3 || change.isPending}
            onClick={() =>
              change.mutate(
                { tenantId: tenant.id, active: nextActive, reason: reason.trim() },
                {
                  onSuccess: (updated) => {
                    setReason("");
                    onChanged(updated);
                  },
                },
              )
            }
          >
            {nextActive ? "Reactivate workspace" : "Suspend workspace"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Audit() {
  const audit = usePlatformAudit(true);
  if (!audit.data || audit.data.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Recent operator actions</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1 text-sm" data-testid="platform-audit">
          {audit.data.slice(0, 20).map((entry) => (
            <li key={entry.id} className="flex gap-2">
              <span className="text-muted-foreground">{formatDateTime(entry.createdAt)}</span>
              <span className="font-medium">{entry.action}</span>
              {typeof entry.detail.reason === "string" && (
                <span className="truncate text-muted-foreground">— {entry.detail.reason}</span>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
