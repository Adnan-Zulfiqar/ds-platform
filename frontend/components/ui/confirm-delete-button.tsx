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
}: {
  label: string;
  onConfirm: () => void;
  disabled?: boolean;
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
        aria-label={`Delete ${label}`}
        onClick={() => setArmed(true)}
      >
        Delete
      </Button>
    );
  }
  return (
    <Button
      size="sm"
      variant="destructive"
      disabled={disabled}
      aria-label={`Confirm delete ${label}`}
      onClick={() => {
        setArmed(false);
        onConfirm();
      }}
    >
      Confirm delete
    </Button>
  );
}
