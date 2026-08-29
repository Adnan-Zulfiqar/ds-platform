"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { ApiError } from "@/lib/api-client";
import { env } from "@/lib/env";
import { requestGoogleNonce, signInWithGoogle } from "@/services/auth";

/**
 * Google's official sign-in button.
 *
 * **Rendered by Google, not by us.** `google.accounts.id.renderButton` draws it,
 * which is what Google's branding terms require and what makes it recognisable
 * to the person clicking. A hand-built button with a Google logo would look the
 * same and be exactly the pattern phishing pages use — so the script either
 * loads and draws the real thing, or no button appears at all.
 *
 * No One Tap: `prompt()` is deliberately not called. It is a separate consent
 * surface that appears unbidden, and this milestone is the button only.
 *
 * The credential never touches storage and is never decoded here. It goes
 * straight from Google's callback to the backend, which is the only thing that
 * can verify the signature — anything this component believed about the token
 * would be an assertion by whoever supplied it.
 */

const GSI_SRC = "https://accounts.google.com/gsi/client";

declare global {
  interface Window {
    google?: {
      accounts?: {
        id?: {
          initialize: (config: Record<string, unknown>) => void;
          renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void;
        };
      };
    };
  }
}

function loadGsiScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.google?.accounts?.id) return Promise.resolve();

  const existing = document.querySelector<HTMLScriptElement>(`script[src="${GSI_SRC}"]`);
  if (existing) {
    return new Promise((resolve, reject) => {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("gsi-load-failed")));
    });
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = GSI_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("gsi-load-failed"));
    document.head.appendChild(script);
  });
}

export interface GoogleSignInButtonProps {
  /** Where to send the browser once a session exists. */
  onSuccess: () => void;
  /** Shown above the button; `null` clears it. */
  onConflict?: (message: string) => void;
  text?: "signin_with" | "signup_with" | "continue_with";
}

export function GoogleSignInButton({
  onSuccess,
  onConflict,
  text = "continue_with",
}: GoogleSignInButtonProps) {
  const container = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "working" | "unavailable">(
    "loading",
  );
  const [error, setError] = useState<string | null>(null);
  const headingId = useId();

  const clientId = env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

  const handleCredential = useCallback(
    async (credential: string, nonce: string) => {
      setStatus("working");
      setError(null);
      try {
        await signInWithGoogle({ credential, nonce });
        onSuccess();
      } catch (caught) {
        // 409 is the deliberate refusal to auto-link an existing local account.
        // It needs its own guidance because the person can act on it, unlike a
        // generic failure.
        //
        // Matched through `ApiError`, which is what the client throws — reading
        // a raw axios `response.status` here silently never matched, and the
        // person got the generic message instead of the one they could act on.
        if (caught instanceof ApiError && caught.status === 409) {
          const message =
            "An account already exists for that email address. Sign in with your " +
            "password, then connect Google from your account settings.";
          setError(message);
          onConflict?.(message);
        } else {
          setError("Google sign-in could not be completed. Please try again.");
        }
        setStatus("idle");
      }
    },
    [onConflict, onSuccess],
  );

  useEffect(() => {
    if (!clientId) {
      // Not configured: offer nothing rather than a button that cannot work.
      setStatus("unavailable");
      return;
    }

    let cancelled = false;

    void (async () => {
      try {
        // The nonce is fetched before the script so the button is never drawn
        // in a state where clicking it would produce a credential we cannot
        // check.
        const { nonce } = await requestGoogleNonce();
        await loadGsiScript();
        if (cancelled || !container.current) return;

        const id = window.google?.accounts?.id;
        if (!id) {
          setStatus("unavailable");
          return;
        }

        id.initialize({
          client_id: clientId,
          nonce,
          callback: (response: { credential?: string }) => {
            if (response.credential) void handleCredential(response.credential, nonce);
          },
          // Explicit: the button only. One Tap is a separate surface.
          auto_select: false,
          cancel_on_tap_outside: true,
        });

        id.renderButton(container.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text,
          shape: "rectangular",
          logo_alignment: "left",
          width: 320,
        });
        if (!cancelled) setStatus("idle");
      } catch {
        if (!cancelled) setStatus("unavailable");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [clientId, handleCredential, text]);

  if (status === "unavailable") {
    return null;
  }

  return (
    <div className="space-y-3" data-testid="google-sign-in">
      {error && (
        <Alert variant="destructive" role="alert">
          <AlertDescription data-testid="google-error">{error}</AlertDescription>
        </Alert>
      )}

      {/* Google draws into this element. Nothing else writes to it. */}
      <div
        ref={container}
        aria-labelledby={headingId}
        className="flex min-h-[44px] justify-center"
        data-testid="google-button-container"
      />
      <span id={headingId} className="sr-only">
        Continue with Google
      </span>

      <p aria-live="polite" className="sr-only">
        {status === "loading" && "Loading Google sign-in."}
        {status === "working" && "Signing you in with Google."}
      </p>
    </div>
  );
}
