"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import { formatDateTime } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { logoutAll, requestEmailVerification } from "@/services/auth";

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again.";
}

/**
 * Settings → Profile. Until this page existed the user menu's "Profile"
 * link went to a 404 (found by the 2026-10-04 analysis).
 *
 * Everything here is read from the identity the app already holds, and the
 * two actions use endpoints that already existed without a UI: email
 * verification and "sign out everywhere". There is no name or password
 * editing because the API has no endpoint for either; password changes go
 * through the reset-by-code flow, which is linked rather than duplicated.
 */
export function ProfileWorkspace() {
  const { identity, logout } = useAuth();
  const verify = useMutation({ mutationFn: requestEmailVerification });
  const signOutEverywhere = useMutation({
    mutationFn: async () => {
      const message = await logoutAll();
      // The server has revoked every refresh token, including this tab's;
      // the local sign-out clears the in-memory state and returns to /login.
      await logout();
      return message;
    },
  });

  if (!identity) return <Skeleton className="h-64 w-full" />;
  const { user, tenant, roles } = identity;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Your details</CardTitle>
          <CardDescription>How you appear to your team.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2" data-testid="profile-details">
            <div>
              <dt className="text-sm text-muted-foreground">Name</dt>
              <dd className="text-sm font-medium">{user.fullName ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted-foreground">Email</dt>
              <dd className="flex flex-wrap items-center gap-2 text-sm font-medium">
                <span>{user.email}</span>
                {user.isVerified ? (
                  <Badge variant="success">Verified</Badge>
                ) : (
                  <Badge variant="warning">Not verified</Badge>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-sm text-muted-foreground">Workspace</dt>
              <dd className="text-sm font-medium">{tenant.name}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted-foreground">Role</dt>
              <dd className="flex flex-wrap gap-1">
                {roles.map((role) => (
                  <Badge key={role} variant="secondary" className="capitalize">
                    {role}
                  </Badge>
                ))}
              </dd>
            </div>
            <div>
              <dt className="text-sm text-muted-foreground">Last sign-in</dt>
              <dd className="text-sm font-medium">{formatDateTime(user.lastLoginAt)}</dd>
            </div>
          </dl>
          {!user.isVerified && (
            <div className="mt-4 space-y-2">
              <Button
                variant="outline"
                disabled={verify.isPending || verify.isSuccess}
                onClick={() => verify.mutate()}
              >
                {verify.isPending ? "Sending…" : "Send verification email"}
              </Button>
              {verify.isSuccess && (
                <p className="text-sm text-muted-foreground" role="status">
                  Check your inbox for the verification link.
                </p>
              )}
              {verify.isError && (
                <Alert variant="destructive">
                  <AlertDescription>{errorText(verify.error)}</AlertDescription>
                </Alert>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Security</CardTitle>
          <CardDescription>Your password and where you are signed in.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <p className="text-sm">
              To change your password, request a one-time code to your email address.
            </p>
            <Button asChild variant="outline">
              <Link href="/forgot-password">Change password</Link>
            </Button>
          </div>
          <div className="space-y-1">
            <p className="text-sm">
              Signed in somewhere you don&apos;t recognise? Sign out of every device, including
              this one.
            </p>
            <Button
              variant="destructive"
              disabled={signOutEverywhere.isPending}
              onClick={() => signOutEverywhere.mutate()}
            >
              {signOutEverywhere.isPending ? "Signing out…" : "Sign out everywhere"}
            </Button>
            {signOutEverywhere.isError && (
              <Alert variant="destructive">
                <AlertDescription>{errorText(signOutEverywhere.error)}</AlertDescription>
              </Alert>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
