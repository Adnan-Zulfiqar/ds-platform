"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import { usePlatformReauth } from "@/services/platform";

/**
 * D-018: actions that change access need the operator's password and a
 * fresh code within the last ten minutes. The server decides; when it
 * answers `reauth_required`, this asks for them and retries the action once.
 */
type Guard = <T>(action: () => Promise<T>) => Promise<T>;

const ReauthContext = createContext<Guard | null>(null);

export function useReauthGuard(): Guard {
  const guard = useContext(ReauthContext);
  if (!guard)
    throw new Error("useReauthGuard must be used inside <ReauthProvider>");
  return guard;
}

function needsReauth(error: unknown): boolean {
  return error instanceof ApiError && error.code === "reauth_required";
}

export function ReauthProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pending = useRef<{
    resolve: () => void;
    reject: (e: unknown) => void;
  } | null>(null);

  const guard = useCallback<Guard>(async (action) => {
    try {
      return await action();
    } catch (error) {
      if (!needsReauth(error)) throw error;
      await new Promise<void>((resolve, reject) => {
        pending.current = { resolve, reject };
        setOpen(true);
      });
      return action();
    }
  }, []);

  function settle(confirmed: boolean) {
    const waiter = pending.current;
    pending.current = null;
    setOpen(false);
    if (confirmed) waiter?.resolve();
    else
      waiter?.reject(
        new ApiError({
          code: "reauth_cancelled",
          message: "Cancelled.",
          status: null,
        }),
      );
  }

  return (
    <ReauthContext.Provider value={guard}>
      {children}
      <ReauthDialog
        open={open}
        onConfirmed={() => settle(true)}
        onCancel={() => settle(false)}
      />
    </ReauthContext.Provider>
  );
}

function ReauthDialog({
  open,
  onConfirmed,
  onCancel,
}: {
  open: boolean;
  onConfirmed: () => void;
  onCancel: () => void;
}) {
  const reauth = usePlatformReauth();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");

  function reset() {
    setPassword("");
    setCode("");
    reauth.reset();
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    reauth.mutate(
      { password, code },
      {
        onSuccess: () => {
          reset();
          onConfirmed();
        },
        onError: () => setCode(""),
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          reset();
          onCancel();
        }
      }}
    >
      <DialogContent data-testid="platform-reauth-dialog">
        <DialogHeader>
          <DialogTitle>Confirm it is you</DialogTitle>
          <DialogDescription>
            This action changes someone&apos;s access. Enter your password and a
            new code from your authenticator app. It stays confirmed for ten
            minutes in this session.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-3" onSubmit={onSubmit} noValidate>
          <div className="space-y-1">
            <Label htmlFor="platform-reauth-password">Password</Label>
            <Input
              id="platform-reauth-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="platform-reauth-code">New one-time code</Label>
            <Input
              id="platform-reauth-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            />
          </div>
          {reauth.isError && (
            <Alert variant="destructive">
              <AlertDescription>
                Not confirmed. Check the password, and wait for a new code —
                each code works once.
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                reset();
                onCancel();
              }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={reauth.isPending || !password || code.length !== 6}
            >
              {reauth.isPending ? "Confirming…" : "Confirm"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
