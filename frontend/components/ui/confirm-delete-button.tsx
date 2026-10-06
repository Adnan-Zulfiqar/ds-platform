"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

/**
 * Delete in two clicks, no native dialog. `window.confirm` cannot be styled,
 * blocks the tab, and is awkward to drive from tests; a second click within
 * a short window is the same safeguard without those costs, and a misclick
 * simply disarms itself.
 */
export function ConfirmDeleteButton({
  label,
  onConfirm,
  disabled,
  text = "Delete",
  confirmText = "Confirm delete",
}: {
  label: string;
  onConfirm: () => void;
  disabled?: boolean;
  /** The first click's wording; the action need not be a delete. */
  text?: string;
  confirmText?: string;
}) {
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const timer = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(timer);
  }, [armed]);

  if (!armed) {
    return (
      <Button
        size="sm"
        variant="ghost"
        disabled={disabled}
        aria-label={`${text} ${label}`}
        onClick={() => setArmed(true)}
      >
        {text}
      </Button>
    );
  }
  return (
    <Button
      size="sm"
      variant="destructive"
      disabled={disabled}
      aria-label={`${confirmText} ${label}`}
      onClick={() => {
        setArmed(false);
        onConfirm();
      }}
    >
      {confirmText}
    </Button>
  );
}
