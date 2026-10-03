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
import { useAuth } from "@/providers/auth-provider";
import {
  type Invitation,
  useInvitations,
  useInviteMember,
  useRevokeInvitation,
  useUsers,
} from "@/services/users";

const ROLE_HELP: Record<Invitation["role"], string> = {
  admin: "Admin — everything except billing and ownership",
  member: "Member — day-to-day operations",
  viewer: "Viewer — read-only",
};

/**
 * Settings → Team (Track E4). Everyone sees the roster; owners and admins
 * also invite and revoke. The server enforces the same rule — hiding the
 * controls is for clarity, not security.
 */
export function TeamWorkspace() {
  const { hasRole } = useAuth();
  const canManage = hasRole("admin");
  const users = useUsers({ page: 1, size: 100, sortBy: "created_at", sortDir: "asc" });

  return (
    <div className="space-y-6">
      {canManage && <InviteForm />}
      {canManage && <OpenInvitations />}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Members</CardTitle>
        </CardHeader>
        <CardContent>
          {users.isError ? (
            <ErrorState title="Could not load members" onRetry={() => void users.refetch()} />
          ) : users.isLoading || !users.data ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <ul className="divide-y rounded-lg border" data-testid="team-members">
              {users.data.items.map((user) => (
                <li key={user.id} className="flex items-center gap-3 p-3 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{user.fullName ?? user.email}</p>
                    {user.fullName && (
                      <p className="truncate text-muted-foreground">{user.email}</p>
                    )}
                  </div>
                  {!user.isActive && <Badge variant="outline">Inactive</Badge>}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function InviteForm() {
  const invite = useInviteMember();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Invitation["role"]>("member");
  const [sentTo, setSentTo] = useState<string | null>(null);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSentTo(null);
    invite.mutate(
      { email: email.trim(), role },
      {
        onSuccess: (created) => {
          setSentTo(created.email);
          setEmail("");
        },
      },
    );
  }

  const error = invite.error instanceof ApiError ? invite.error.message : invite.error ? "Could not send the invitation." : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Invite a colleague</CardTitle>
        <CardDescription>
          They get an email with a link that works once and expires in 7 days.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={onSubmit} noValidate>
          <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
            <div className="space-y-2">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                autoComplete="off"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="invite-role">Role</Label>
              <select
                id="invite-role"
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={role}
                onChange={(e) => setRole(e.target.value as Invitation["role"])}
              >
                {(Object.keys(ROLE_HELP) as Invitation["role"][]).map((r) => (
                  <option key={r} value={r}>
                    {ROLE_HELP[r]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {sentTo && (
            <p className="text-sm text-muted-foreground" role="status">
              Invitation sent to {sentTo}.
            </p>
          )}
          <Button type="submit" disabled={!email.trim() || invite.isPending}>
            {invite.isPending ? "Sending…" : "Send invitation"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function OpenInvitations() {
  const invitations = useInvitations(true);
  const revoke = useRevokeInvitation();
  // Captured once per mount: "expired" is a hint, not a live countdown.
  const [now] = useState(() => Date.now());

  if (invitations.isError) {
    return <ErrorState title="Could not load invitations" onRetry={() => void invitations.refetch()} />;
  }
  if (!invitations.data || invitations.data.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Open invitations</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="divide-y rounded-lg border" data-testid="team-invitations">
          {invitations.data.map((invitation) => {
            const expired = new Date(invitation.expiresAt).getTime() <= now;
            return (
              <li key={invitation.id} className="flex items-center gap-3 p-3 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{invitation.email}</p>
                  <p className="text-muted-foreground">
                    {invitation.role} ·{" "}
                    {expired ? "expired — send again" : `expires ${formatDateTime(invitation.expiresAt)}`}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(invitation.id)}
                  aria-label={`Revoke invitation for ${invitation.email}`}
                >
                  Revoke
                </Button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
