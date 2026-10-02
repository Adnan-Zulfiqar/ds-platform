"use client";

import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Route-level error boundary.
 *
 * Next.js renders this in place of the segment that threw, so an exception in
 * one panel does not blank the whole application. `reset` re-renders the
 * segment, which recovers from a transient failure without a full reload.
 *
 * This file must be a Client Component — error boundaries rely on React state.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Replace with the real error reporter (Sentry et al.) when observability
    // is wired up. Logging here rather than in render because render may run
    // more than once for the same error.
    console.error("Unhandled route error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[400px] items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10">
            <AlertCircle className="h-5 w-5 text-destructive" />
          </div>
          <CardTitle>Something went wrong</CardTitle>
          <CardDescription>
            An unexpected error occurred. You can try again, and if the problem
            persists please contact support.
          </CardDescription>
        </CardHeader>

        {/* The digest is a server-generated identifier for the error. The
            message itself is withheld in production by Next.js because it can
            contain internal detail, so the digest is what support can trace. */}
        {error.digest && (
          <CardContent>
            <p className="text-xs text-muted-foreground">
              Reference: <code className="font-mono">{error.digest}</code>
            </p>
          </CardContent>
        )}

        <CardFooter className="gap-2">
          <Button onClick={reset}>Try again</Button>
          {/* Leaving the failed segment through the router unmounts it, which
              clears this error boundary; no full page load is needed. */}
          <Button variant="outline" asChild>
            <Link href="/">Go home</Link>
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
