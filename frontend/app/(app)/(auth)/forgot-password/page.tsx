"use client";

import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  completePasswordReset,
  requestPasswordReset,
  verifyPasswordResetCode,
} from "@/services/auth";

/**
 * Password reset in three steps: address, code, new password.
 *
 * **Nothing here ever reveals whether an address has an account.** Step one
 * always advances to step two, because the backend always returns a challenge —
 * an unknown address simply gets one that will never verify. A page that
 * stopped and said "no account found" would be a membership list.
 *
 * The success state is only ever shown after the backend confirms. There is no
 * optimistic "check your email" before the request completes, because the
 * request is the only thing that knows whether a code was sent.
 */

type Step = "request" | "verify" | "reset" | "done";

const RESEND_COOLDOWN_SECONDS = 60;

function describeError(caught: unknown, fallback: string): string {
  const response = (caught as { response?: { data?: { error?: { message?: string } } } })
    .response;
  return response?.data?.error?.message ?? fallback;
}

export default function ForgotPasswordPage() {
  const router = useRouter();

  const [step, setStep] = useState<Step>("request");
  const [email, setEmail] = useState("");
  const [challengeId, setChallengeId] = useState("");
  const [code, setCode] = useState("");
  const [ticket, setTicket] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  // Focus moves to the new step's first field so a keyboard or screen-reader
  // user is not left at the top of a page that has changed underneath them.
  const codeInput = useRef<HTMLInputElement>(null);
  const passwordInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (step === "verify") codeInput.current?.focus();
    if (step === "reset") passwordInput.current?.focus();
  }, [step]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((n) => n - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  const submitEmail = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setError(null);
      try {
        const challenge = await requestPasswordReset(email);
        setChallengeId(challenge.challengeId);
        setNotice(challenge.message);
        setCooldown(RESEND_COOLDOWN_SECONDS);
        // Always advances, whether or not the address is known.
        setStep("verify");
      } catch (caught) {
        setError(describeError(caught, "Could not start a reset. Please try again."));
      } finally {
        setBusy(false);
      }
    },
    [email],
  );

  const resend = useCallback(async () => {
    if (cooldown > 0) return;
    setBusy(true);
    setError(null);
    try {
      const challenge = await requestPasswordReset(email);
      setChallengeId(challenge.challengeId);
      setNotice(challenge.message);
      setCooldown(RESEND_COOLDOWN_SECONDS);
      setCode("");
    } catch (caught) {
      setError(describeError(caught, "Could not send another code. Please try again."));
    } finally {
      setBusy(false);
    }
  }, [cooldown, email]);

  const submitCode = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setError(null);
      try {
        const issued = await verifyPasswordResetCode({ challengeId, code });
        setTicket(issued.resetTicket);
        setStep("reset");
      } catch (caught) {
        // Wrong, expired and exhausted are one message, matching the backend.
        setError(describeError(caught, "That code is not valid. Request a new one."));
      } finally {
        setBusy(false);
      }
    },
    [challengeId, code],
  );

  const submitPassword = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (password !== confirmation) {
        setError("Those passwords do not match.");
        return;
      }
      setBusy(true);
      setError(null);
      try {
        await completePasswordReset({ resetTicket: ticket, newPassword: password });
        setStep("done");
      } catch (caught) {
        setError(describeError(caught, "Could not set that password. Please try again."));
      } finally {
        setBusy(false);
      }
    },
    [confirmation, password, ticket],
  );

  return (
    <Card className="w-full" data-testid="forgot-password-card">
      <CardHeader className="space-y-1">
        <CardTitle className="text-2xl">Reset your password</CardTitle>
        <CardDescription>
          {step === "request" && "We will send a six-digit code to your email address."}
          {step === "verify" && "Enter the six-digit code we sent."}
          {step === "reset" && "Choose a new password."}
          {step === "done" && "Your password has been updated."}
        </CardDescription>
      </CardHeader>

      {/* One live region for the whole flow, so a screen reader announces each
          change once rather than per-step. */}
      <p aria-live="polite" className="sr-only" data-testid="reset-status">
        {busy ? "Working" : (error ?? notice ?? "")}
      </p>

      {step === "request" && (
        <form onSubmit={submitEmail} noValidate>
          <CardContent className="space-y-4">
            {error && (
              <Alert variant="destructive" role="alert">
                <AlertDescription data-testid="reset-error">{error}</AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <Label htmlFor="reset-email">Email</Label>
              <Input
                id="reset-email"
                data-testid="reset-email"
                type="email"
                autoComplete="email"
                placeholder="you@company.com"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          </CardContent>
          <CardFooter className="flex-col gap-4">
            <Button type="submit" className="w-full" disabled={busy || !email}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {busy ? "Sending..." : "Send code"}
            </Button>
            <BackToSignIn />
          </CardFooter>
        </form>
      )}

      {step === "verify" && (
        <form onSubmit={submitCode} noValidate>
          <CardContent className="space-y-4">
            {notice && (
              <Alert>
                <AlertDescription data-testid="reset-notice">{notice}</AlertDescription>
              </Alert>
            )}
            {error && (
              <Alert variant="destructive" role="alert">
                <AlertDescription data-testid="reset-error">{error}</AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <Label htmlFor="reset-code">Six-digit code</Label>
              <Input
                id="reset-code"
                data-testid="reset-code"
                ref={codeInput}
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                placeholder="000000"
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                className="tracking-[0.4em]"
              />
            </div>
          </CardContent>
          <CardFooter className="flex-col gap-4">
            <Button type="submit" className="w-full" disabled={busy || code.length !== 6}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {busy ? "Checking..." : "Verify code"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="w-full"
              data-testid="reset-resend"
              onClick={resend}
              disabled={busy || cooldown > 0}
            >
              {cooldown > 0 ? `Resend in ${cooldown}s` : "Send another code"}
            </Button>
            <BackToSignIn />
          </CardFooter>
        </form>
      )}

      {step === "reset" && (
        <form onSubmit={submitPassword} noValidate>
          <CardContent className="space-y-4">
            {error && (
              <Alert variant="destructive" role="alert">
                <AlertDescription data-testid="reset-error">{error}</AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <Label htmlFor="reset-password">New password</Label>
              <Input
                id="reset-password"
                data-testid="reset-password"
                ref={passwordInput}
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="reset-confirm">Confirm new password</Label>
              <Input
                id="reset-confirm"
                data-testid="reset-confirm"
                type="password"
                autoComplete="new-password"
                required
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
              />
            </div>
          </CardContent>
          <CardFooter className="flex-col gap-4">
            <Button type="submit" className="w-full" disabled={busy || !password}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {busy ? "Saving..." : "Set new password"}
            </Button>
            <BackToSignIn />
          </CardFooter>
        </form>
      )}

      {step === "done" && (
        <>
          <CardContent className="space-y-4">
            <Alert data-testid="reset-done">
              <AlertDescription>
                Your password has been updated, and every existing session has been
                signed out.
              </AlertDescription>
            </Alert>
          </CardContent>
          <CardFooter>
            <Button className="w-full" onClick={() => router.push("/login")}>
              Go to sign in
            </Button>
          </CardFooter>
        </>
      )}
    </Card>
  );
}

function BackToSignIn() {
  return (
    <Link
      href="/login"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="h-3.5 w-3.5" />
      Back to sign in
    </Link>
  );
}
