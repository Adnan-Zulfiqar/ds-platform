"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useForm } from "react-hook-form";

import { useRouter } from "next/navigation";

import { GoogleSignInButton } from "@/components/auth/google-sign-in-button";
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
import { ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import {
  estimatePasswordStrength,
  registerSchema,
  type RegisterFormValues,
} from "@/lib/validation/auth";
import { useAuth } from "@/providers/auth-provider";

const STRENGTH_COLOURS = [
  "bg-destructive",
  "bg-destructive",
  "bg-warning",
  "bg-warning",
  "bg-success",
] as const;

export default function RegisterPage() {
  const { register: registerAccount } = useAuth();
  const [formError, setFormError] = useState<string | null>(null);

  const router = useRouter();
  const form = useForm<RegisterFormValues>({
    resolver: zodResolver(registerSchema),
    // Validate as the user types, but only after the first blur. Validating on
    // every keystroke from empty flags "required" before they have had a chance
    // to type anything, which reads as the form scolding them.
    mode: "onTouched",
    defaultValues: {
      companyName: "",
      firstName: "",
      lastName: "",
      email: "",
      password: "",
      confirmPassword: "",
    },
  });

  const password = form.watch("password");
  const strength = estimatePasswordStrength(password);

  async function onSubmit(values: RegisterFormValues) {
    setFormError(null);
    try {
      await registerAccount({
        companyName: values.companyName,
        email: values.email,
        password: values.password,
        firstName: values.firstName || undefined,
        lastName: values.lastName || undefined,
      });
    } catch (error) {
      if (error instanceof ApiError) {
        // Map field-level errors from the server onto the matching inputs, so
        // a server-only rule (a password the client policy allowed) is shown
        // against the field rather than as a detached banner.
        const fieldErrors = error.fieldErrors;
        let matched = false;
        for (const [field, message] of Object.entries(fieldErrors)) {
          if (field in values) {
            form.setError(field as keyof RegisterFormValues, { message });
            matched = true;
          }
        }
        if (!matched) setFormError(error.message);
      } else {
        setFormError("Could not create your account. Please try again.");
      }
    }
  }

  const isSubmitting = form.formState.isSubmitting;

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="text-2xl">Create your account</CardTitle>
        <CardDescription>
          Start automating your dropshipping operation.
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

            <FormField
              control={form.control}
              name="companyName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Company name</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="organization"
                      placeholder="Acme Trading"
                      disabled={isSubmitting}
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    This becomes your workspace name.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="firstName"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>First name</FormLabel>
                    <FormControl>
                      <Input
                        autoComplete="given-name"
                        disabled={isSubmitting}
                        {...field}
                      />
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
                      <Input
                        autoComplete="family-name"
                        disabled={isSubmitting}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Work email</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      autoComplete="email"
                      autoCapitalize="none"
                      spellCheck={false}
                      placeholder="you@company.com"
                      disabled={isSubmitting}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

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

                  {password && (
                    <div className="space-y-1">
                      <div
                        className="flex gap-1"
                        // Decorative: the label beneath carries the meaning, so
                        // the bars would only add noise to a screen reader.
                        aria-hidden="true"
                      >
                        {[0, 1, 2, 3].map((index) => (
                          <div
                            key={index}
                            className={cn(
                              "h-1 flex-1 rounded-full transition-colors",
                              index < strength.score
                                ? STRENGTH_COLOURS[strength.score]
                                : "bg-muted",
                            )}
                          />
                        ))}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Password strength: {strength.label}
                      </p>
                    </div>
                  )}

                  <FormDescription>
                    At least 12 characters, with upper and lower case letters and
                    a digit.
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
          </CardContent>

          <CardFooter className="flex-col gap-4">
            <Button type="submit" className="w-full" disabled={isSubmitting}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {isSubmitting ? "Creating account..." : "Create account"}
            </Button>


            {/* Google's own button renders below. The divider is ours; the
                button is not, because a look-alike is the pattern phishing
                pages use. */}
            <div className="flex w-full items-center gap-3" aria-hidden="true">
              <span className="h-px flex-1 bg-border" />
              <span className="text-xs uppercase text-muted-foreground">or</span>
              <span className="h-px flex-1 bg-border" />
            </div>

            <div className="w-full">
              <GoogleSignInButton
                onSuccess={() => router.replace("/dashboard")}
                text="signup_with"
              />
            </div>

            <p className="text-center text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link href="/login" className="text-primary hover:underline">
                Sign in
              </Link>
            </p>
          </CardFooter>
        </form>
      </Form>
    </Card>
  );
}
