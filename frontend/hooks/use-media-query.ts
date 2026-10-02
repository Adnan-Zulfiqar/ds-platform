"use client";

import { useSyncExternalStore } from "react";

/**
 * Track a CSS media query from React.
 *
 * Used for behaviour that cannot be expressed in CSS — closing the mobile
 * drawer when the viewport grows past the breakpoint, for example. **Layout
 * itself should stay in Tailwind's responsive classes**, which work during
 * server rendering and before hydration; this hook cannot.
 *
 * Returns `false` on the server and on the first client render, because there
 * is no viewport to measure until the browser takes over. Consumers must treat
 * `false` as "not yet known" rather than "definitely not matching", or they
 * will flash the wrong layout for one frame.
 */
export function useMediaQuery(query: string): boolean {
  // A media query is an external store: subscribe to its change event and
  // read it on demand. The server snapshot (`false`) is what the first
  // client render also uses, so hydration agrees; React then re-reads the
  // real value without an effect setting state.
  return useSyncExternalStore(
    (onChange) => {
      const mediaQueryList = window.matchMedia(query);
      mediaQueryList.addEventListener("change", onChange);
      return () => mediaQueryList.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

const noSubscription = () => () => {};

/**
 * `false` during server rendering and hydration, `true` afterwards — for UI
 * that cannot be known on the server (the resolved theme). Replaces the
 * `useEffect(() => setMounted(true), [])` pattern without setting state in an
 * effect.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
}

/** Tailwind's `md` breakpoint. Matches the sidebar's own visibility rule. */
export const MD_BREAKPOINT_QUERY = "(min-width: 768px)";

/** Tailwind's `lg` breakpoint — desktop checklist aside becomes visible here. */
export const LG_BREAKPOINT_QUERY = "(min-width: 1024px)";

export function useIsDesktop(): boolean {
  return useMediaQuery(MD_BREAKPOINT_QUERY);
}

/** True when the viewport is at or above Tailwind `lg` (1024px). */
export function useIsLgUp(): boolean {
  return useMediaQuery(LG_BREAKPOINT_QUERY);
}
