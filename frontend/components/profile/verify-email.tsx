"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useSyncExternalStore } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/providers/auth-provider";
import { confirmEmailVerification } from "@/services/auth";

/**
 * Consumes the token from the verification email. The token arrives in the
 * URL fragment (`/verify-email#token=…`), like the invitation link, so it is
 * never sent to the server as part of the request path. Until 2026-10-10
 * the email carried a token and no page could use it.
 */

function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function readToken(): string | null {
  const match = /(?:^#|&)token=([\w.-]+)/.exec(window.location.hash);
  return match?.[1] ?? null;
}

export function VerifyEmail() {
  const { identity, refreshIdentity } = useAuth();
  const token = useSyncExternalStore(subscribe, readToken, () => null);
  const confirm = useMutation({
    mutationFn: confirmEmailVerification,
    onSuccess: () => refreshIdentity(),
  });
  const alreadyVerified = identity?.user.isVerified ?? false;

  useEffect(() => {
    if (token && !alreadyVerified && confirm.isIdle) confirm.mutate(token);
    // `confirm` is stable for the component's life; the token is the input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, alreadyVerified]);

  return (
    <Card className="max-w-lg" data-testid="verify-email">
      <CardHeader>
        <CardTitle className="text-base">Email verification</CardTitle>
        <CardDescription>
          Confirms the address on your DropPilot account.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {alreadyVerified || confirm.isSuccess ? (
          <>
            <Alert>
              <AlertDescription>
                Your email address is verified. Thank you.
              </AlertDescription>
            </Alert>
            <Button asChild size="sm">
              <Link href="/dashboard">Go to the dashboard</Link>
            </Button>
          </>
        ) : token === null ? (
          <>
            <p className="text-muted-foreground">
              This page needs the link from your verification email. Open the
              email and use its link, or request a new one from your profile.
            </p>
            <Button asChild size="sm" variant="outline">
              <Link href="/settings/profile">Go to profile</Link>
            </Button>
          </>
        ) : confirm.isError ? (
          <>
            <Alert variant="destructive">
              <AlertDescription>
                This link is not valid any more. Links expire, and each one
                works once. Request a new email from your profile.
              </AlertDescription>
            </Alert>
            <Button asChild size="sm" variant="outline">
              <Link href="/settings/profile">Go to profile</Link>
            </Button>
          </>
        ) : (
          <Skeleton className="h-10 w-full" aria-label="Verifying" />
        )}
      </CardContent>
    </Card>
  );
}
