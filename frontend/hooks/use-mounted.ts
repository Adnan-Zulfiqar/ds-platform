"use client";

import { useEffect, useState } from "react";

/**
 * Whether the component has completed its first client render.
 *
 * The guard for anything the server cannot know: the resolved theme, the
 * viewport size, `localStorage` contents. Rendering those during SSR produces
 * markup that disagrees with the client and triggers a hydration error.
 *
 * Use it to render a stable placeholder until mounted, not to skip rendering
 * entirely — an element that appears only after hydration causes layout shift.
 */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}
