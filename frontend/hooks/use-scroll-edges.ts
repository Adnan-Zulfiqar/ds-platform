"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";

export interface ScrollEdges {
  /** Content continues above the visible area. */
  top: boolean;
  /** Content continues below the visible area. */
  bottom: boolean;
  /** Content continues to the left of the visible area. */
  left: boolean;
  /** Content continues to the right of the visible area. */
  right: boolean;
}

const NONE: ScrollEdges = { top: false, bottom: false, left: false, right: false };

/**
 * Report whether a scroll container has more content beyond each of its
 * edges.
 *
 * Exists for the navigation lists (UX-L2D-02): at common laptop heights the
 * last items scroll out of view, and a list that ends exactly at the fold
 * looks complete when it is not. UX-L2D-05 added the horizontal pair for
 * the editor's section strip, which has the same problem sideways. The
 * consumer paints a fade on the edge that hides content, so "there is more"
 * is visible before anyone scrolls.
 *
 * Measured on scroll and on resize of the container itself (a
 * `ResizeObserver`, because the container's size changes with the viewport
 * and with the sidebar collapsing, neither of which fires `window.resize`
 * reliably). One pixel of tolerance absorbs sub-pixel rounding.
 */
export function useScrollEdges(ref: RefObject<HTMLElement | null>): ScrollEdges {
  const [edges, setEdges] = useState<ScrollEdges>(NONE);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const next: ScrollEdges = {
      top: el.scrollTop > 1,
      bottom: el.scrollHeight - el.clientHeight - el.scrollTop > 1,
      left: el.scrollLeft > 1,
      right: el.scrollWidth - el.clientWidth - el.scrollLeft > 1,
    };
    setEdges((prev) =>
      prev.top === next.top &&
      prev.bottom === next.bottom &&
      prev.left === next.left &&
      prev.right === next.right
        ? prev
        : next,
    );
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
