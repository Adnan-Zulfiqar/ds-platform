"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, MailCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
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
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  forgotPasswordSchema,
  type ForgotPasswordFormValues,
} from "@/lib/validation/auth";

/**
 * Password reset request.
 *
 * **The backend endpoint does not exist yet.** Password reset needs email
 * delivery, a signed single-use token, and its own expiry policy — none of
 * which is in Phase 1 scope. The page is built now because it is linked from
 * the sign-in form and a dead link there is worse than an honest one.
 *
 * The form therefore does not call the API. It states plainly that the feature
 * is not available rather than pretending to send an email that will never
 * arrive — a fake confirmation would leave a locked-out user waiting instead of
 * contacting support.
 */
export default function ForgotPasswordPage() {
  const [submitted, setSubmitted] = useState(false);

  const form = useForm<ForgotPasswordFormValues>({
    resolver: zodResolver(forgotPasswordSchema),
    defaultValues: { email: "" },
  });

  function onSubmit() {
    setSubmitted(true);
  }

  if (submitted) {
    return (
      <Card>
        <CardHeader className="space-y-1">
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-muted">
            <MailCheck className="h-5 w-5 text-muted-foreground" />
          </div>
          <CardTitle className="text-2xl">Not available yet</CardTitle>
          <CardDescription>
            Self-service password reset is not implemented in this release.
          </CardDescription>
        </CardHeader>

        <CardContent>
          <Alert>
            <AlertDescription>
              No email has been sent. To regain access to your account, please
              contact your workspace owner or our support team.
            </AlertDescription>
          </Alert>
        </CardContent>

        <CardFooter>
          <Button asChild variant="outline" className="w-full">
            <Link href="/login">
              <ArrowLeft className="h-4 w-4" />
              Back to sign in
            </Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="text-2xl">Reset your password</CardTitle>
        <CardDescription>
          Enter the email address associated with your account.
        </CardDescription>
      </CardHeader>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
          <CardContent className="space-y-4">
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      autoComplete="email"
                      autoCapitalize="none"
                      spellCheck={false}
                      placeholder="you@company.com"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>

          <CardFooter className="flex-col gap-4">
            <Button type="submit" className="w-full">
              Continue
            </Button>
            <Link
              href="/login"
              className="text-sm text-muted-foreground hover:underline"
            >
              Back to sign in
            </Link>
          </CardFooter>
        </form>
      </Form>
    </Card>
  );
}
