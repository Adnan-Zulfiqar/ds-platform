"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "@/lib/api-client";

/**
 * Unsaved-work and conflict handling for rule-management forms.
 *
 * Deliberately not imported from the draft editor. M2A's conflict machinery is
 * built around an autosaving document with per-tab dirty tracking; a settings
 * form is a different shape — explicit save, no autosave, one form at a time.
 * What is reused is the *principle*, which M2A established the hard way:
 *
 * * The concurrency token is whatever the server last returned, echoed back
 *   verbatim. Never a clock reading, never a value the client computed.
 * * A 409 is a state to resolve, not an error to dismiss. The merchant chooses
 *   to reload or to keep their edits; nothing resolves it silently.
 * * "Saved" is only shown after the server said so.
 */

export type SaveState = "idle" | "saving" | "saved" | "error" | "conflict";

export interface ConflictState {
  /** The token the save was attempted with — now known to be stale. */
  attemptedToken: string;
  message: string;
}

export interface RuleFormState<T> {
  values: T;
  setValues: (next: T | ((current: T) => T)) => void;
  setField: <K extends keyof T>(key: K, value: T[K]) => void;
  isDirty: boolean;
  saveState: SaveState;
  error: ApiError | null;
  conflict: ConflictState | null;
  fieldErrors: Record<string, string>;
  /** Run a save. Returns the server's row on success, `null` on failure. */
  submit: <R>(run: (values: T) => Promise<R>) => Promise<R | null>;
  /** Adopt the server's version, discarding local edits. */
  reset: (next: T) => void;
  /** Abandon the conflict banner without adopting anything. */
  dismissConflict: () => void;
}

function stableEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function useRuleForm<T extends object>(initial: T): RuleFormState<T> {
  const [values, setValuesRaw] = useState<T>(initial);
  const [baseline, setBaseline] = useState<T>(initial);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [error, setError] = useState<ApiError | null>(null);
  const [conflict, setConflict] = useState<ConflictState | null>(null);

  // Re-seed when the caller swaps which rule is being edited. Keyed on the
  // baseline identity rather than a deep compare so that typing — which
  // changes `values` but not `initial` — never resets the form under the
  // merchant's cursor.
  const initialRef = useRef(initial);
  useEffect(() => {
    if (initialRef.current === initial) return;
    initialRef.current = initial;
    setValuesRaw(initial);
    setBaseline(initial);
    setSaveState("idle");
    setError(null);
    setConflict(null);
  }, [initial]);

  const setValues = useCallback((next: T | ((current: T) => T)) => {
    setValuesRaw((current) =>
      typeof next === "function" ? (next as (c: T) => T)(current) : next,
    );
    // Any edit invalidates a "saved" badge: leaving it up next to changed
    // values is a false claim that they are persisted.
    setSaveState((state) => (state === "saved" ? "idle" : state));
  }, []);

  const setField = useCallback(
    <K extends keyof T>(key: K, value: T[K]) => {
      setValues((current) => ({ ...current, [key]: value }));
    },
    [setValues],
  );

  const isDirty = useMemo(
    () => !stableEqual(values, baseline),
    [values, baseline],
  );

  const submit = useCallback(
    async <R,>(run: (current: T) => Promise<R>): Promise<R | null> => {
      setSaveState("saving");
      setError(null);
      try {
        const result = await run(values);
        setBaseline(values);
        setSaveState("saved");
        setConflict(null);
        return result;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setConflict({
            attemptedToken: "",
            message:
              err.message ||
              "This rule was changed by someone else after you opened it.",
          });
          setSaveState("conflict");
        } else {
          setError(
            err instanceof ApiError
              ? err
              : new ApiError({
                  code: "unknown",
                  message: "The rule could not be saved.",
                  status: null,
                }),
          );
          setSaveState("error");
        }
        return null;
      }
    },
    [values],
  );

  const reset = useCallback((next: T) => {
    initialRef.current = next;
    setValuesRaw(next);
    setBaseline(next);
    setSaveState("idle");
    setError(null);
    setConflict(null);
  }, []);

  const dismissConflict = useCallback(() => setConflict(null), []);

  return {
    values,
    setValues,
    setField,
    isDirty,
    saveState,
    error,
    conflict,
    fieldErrors: error?.fieldErrors ?? {},
    submit,
    reset,
    dismissConflict,
  };
}

/**
 * Warn before a reload or tab close discards unsaved edits.
 *
 * Only covers browser-level navigation — `beforeunload` cannot see a Next.js
 * client-side route change. In-app navigation is guarded by the caller, which
 * knows what "leaving" means for its own layout.
 */
export function useUnsavedWarning(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Assigning returnValue is what actually triggers the prompt in
      // Chromium; the text itself has been ignored by browsers for years.
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [active]);
}
