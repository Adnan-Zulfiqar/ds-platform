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
