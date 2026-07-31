import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind class names, resolving conflicts in favour of the last value.
 *
 * `clsx` handles conditional and array inputs; `twMerge` then resolves
 * conflicting utilities. Without the second step, `cn("p-2", "p-4")` emits both
 * classes and the winner depends on stylesheet order rather than call order —
 * which silently breaks the variant-plus-override pattern every component here
 * relies on.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * Render an ISO timestamp in the viewer's locale and timezone.
 *
 * The backend serialises UTC; the browser is the only party that knows where
 * the viewer actually is. `undefined` locale defers to it. Null-safe because
 * half the timestamps in the order domain are legitimately absent (an order
 * that has not shipped has no `shippedAt`).
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** Money formatting: the amount stays the string the backend sent. */
export function formatMoney(
  amount: string | null | undefined,
  currency: string | null | undefined,
): string {
  if (!amount) return "—";
  return currency ? `${currency} ${amount}` : amount;
}
