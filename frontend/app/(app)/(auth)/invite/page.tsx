"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState, useSyncExternalStore } from "react";
import { useForm } from "react-hook-form";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  acceptInvitationSchema,
  type AcceptInvitationFormValues,
} from "@/lib/validation/auth";
import { useAuth } from "@/providers/auth-provider";
import { useInvitationPreview } from "@/services/auth";

/**
 * Track E4: accept a team invitation.
 *
 * The token arrives in the URL **fragment** (`/invite#token=…`), which the
 * browser never sends to a server — so it stays out of access logs and out
 * of the Referer of anything this page loads. It is read on the client only;
 * the server snapshot is `null`, so hydration never mismatches.
 */
function subscribe(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function readToken(): string | null {
  const match = /(?:^#|&)token=([\w.-]+)/.exec(window.location.hash);
  return match?.[1] ?? null;
}

export default function AcceptInvitationPage() {
  const token = useSyncExternalStore(subscribe, readToken, () => null);
  const preview = useInvitationPreview(token);

  if (token === null) {
    return (
      <DeadLink message="This page needs the link from your invitation email. Open the email and use its button." />
    );
  }
  if (preview.isError) {
    return <DeadLink message="This invitation link is invalid, expired or already used. Ask the person who invited you to send a new one." />;
  }
  if (!preview.data) {
    return <Skeleton className="h-96 w-full" />;
  }
  return (
    <AcceptForm
      token={token}
      email={preview.data.email}
      role={preview.data.role}
      workspaceName={preview.data.workspaceName}
    />
  );
}

function DeadLink({ message }: { message: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-2xl">Invitation</CardTitle>
      </CardHeader>
      <CardContent>
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{message}</AlertDescription>
        </Alert>
      </CardContent>
      <CardFooter>
        <Link href="/login" className="text-sm text-primary hover:underline">
          Go to sign in
        </Link>
      </CardFooter>
    </Card>
  );
}

function AcceptForm({
  token,
  email,
  role,
  workspaceName,
}: {
  token: string;
  email: string;
  role: string;
  workspaceName: string;
}) {
  const { acceptInvitation } = useAuth();
  const [accepted, setAccepted] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<AcceptInvitationFormValues>({
    resolver: zodResolver(acceptInvitationSchema),
    mode: "onTouched",
    defaultValues: { firstName: "", lastName: "", password: "", confirmPassword: "" },
  });

  async function onSubmit(values: AcceptInvitationFormValues) {
    setFormError(null);
    try {
      await acceptInvitation({
        token,
        password: values.password,
        firstName: values.firstName || undefined,
        lastName: values.lastName || undefined,
        acceptedLegal: accepted,
      });
    } catch (error) {
      if (error instanceof ApiError) {
        const fieldErrors = error.fieldErrors;
        if (fieldErrors.password) {
          form.setError("password", { message: fieldErrors.password });
        } else {
          setFormError(error.message);
        }
      } else {
        setFormError("Could not accept the invitation. Please try again.");
      }
    }
  }

  const isSubmitting = form.formState.isSubmitting;

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="text-2xl">Join {workspaceName}</CardTitle>
        <CardDescription>
          You were invited as <strong>{role}</strong> with{" "}
          <span className="font-medium">{email}</span>.
        </CardDescription>
      </CardHeader>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
          <CardContent className="space-y-4">
            {formError && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{formError}</AlertDescription>
              </Alert>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="firstName"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>First name</FormLabel>
                    <FormControl>
                      <Input autoComplete="given-name" disabled={isSubmitting} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="lastName"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Last name</FormLabel>
                    <FormControl>
                      <Input autoComplete="family-name" disabled={isSubmitting} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Password</FormLabel>
                  <FormControl>
                    <Input
                      type="password"
                      autoComplete="new-password"
                      disabled={isSubmitting}
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    At least 12 characters, with upper and lower case letters and a digit.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="confirmPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Confirm password</FormLabel>
                  <FormControl>
                    <Input
                      type="password"
                      autoComplete="new-password"
                      disabled={isSubmitting}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {/* Not pre-ticked; the server refuses a false or stale acceptance. */}
            <label
              className="flex items-start gap-2 text-sm text-muted-foreground"
              htmlFor="accept-legal"
            >
              <input
                id="accept-legal"
                data-testid="accept-legal"
                type="checkbox"
                checked={accepted}
                onChange={(e) => setAccepted(e.target.checked)}
                className="mt-1 h-4 w-4 rounded border-input"
              />
              <span>
                I accept the{" "}
                <Link href="/terms" className="text-primary hover:underline">
                  Terms of Service
                </Link>{" "}
                and have read the{" "}
                <Link href="/privacy" className="text-primary hover:underline">
                  Privacy Notice
                </Link>
                .
              </span>
            </label>
          </CardContent>
          <CardFooter>
            <Button type="submit" className="w-full" disabled={isSubmitting || !accepted}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {isSubmitting ? "Joining..." : "Join workspace"}
            </Button>
          </CardFooter>
        </form>
      </Form>
    </Card>
  );
}
