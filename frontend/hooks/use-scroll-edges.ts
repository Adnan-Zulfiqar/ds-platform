"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";

export interface ScrollEdges {
  /** Content continues above the visible area. */
  top: boolean;
  /** Content continues below the visible area. */
  bottom: boolean;
}

/**
 * Report whether a scroll container has more content above or below what is
 * visible.
 *
 * Exists for the navigation lists (UX-L2D-02): at common laptop heights the
 * last items scroll out of view, and a list that ends exactly at the fold
 * looks complete when it is not. The consumer paints a fade on the edge that
 * hides content, so "there is more" is visible before anyone scrolls.
 *
 * Measured on scroll and on resize of the container itself (a
 * `ResizeObserver`, because the container's height changes with the viewport
 * and with the sidebar collapsing, neither of which fires `window.resize`
 * reliably). One pixel of tolerance absorbs sub-pixel rounding.
 */
export function useScrollEdges(ref: RefObject<HTMLElement | null>): ScrollEdges {
  const [edges, setEdges] = useState<ScrollEdges>({ top: false, bottom: false });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const top = el.scrollTop > 1;
    const bottom = el.scrollHeight - el.clientHeight - el.scrollTop > 1;
    setEdges((prev) => (prev.top === top && prev.bottom === bottom ? prev : { top, bottom }));
  }, [ref]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      observer.disconnect();
    };
  }, [ref, measure]);

  return edges;
}
