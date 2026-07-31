"use client";

import * as SeparatorPrimitive from "@radix-ui/react-separator";
import {
  forwardRef,
  type ComponentPropsWithoutRef,
  type ElementRef,
} from "react";

import { cn } from "@/lib/utils";

/**
 * Visual divider.
 *
 * Defaults to `decorative`, which removes it from the accessibility tree. That
 * is correct for a line drawn purely to group things visually — announcing
 * "separator" between every menu item is noise. Pass `decorative={false}` only
 * when the divider genuinely conveys a structural boundary that a screen reader
 * user needs to know about.
 */
const Separator = forwardRef<
  ElementRef<typeof SeparatorPrimitive.Root>,
  ComponentPropsWithoutRef<typeof SeparatorPrimitive.Root>
>(
  (
    { className, orientation = "horizontal", decorative = true, ...props },
    ref,
  ) => (
    <SeparatorPrimitive.Root
      ref={ref}
      decorative={decorative}
      orientation={orientation}
      className={cn(
        "shrink-0 bg-border",
        orientation === "horizontal" ? "h-[1px] w-full" : "h-full w-[1px]",
        className,
      )}
      {...props}
    />
  ),
);
Separator.displayName = SeparatorPrimitive.Root.displayName;

export { Separator };
