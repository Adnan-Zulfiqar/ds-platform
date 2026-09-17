"use client";

import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { AttentionItem } from "./home-rules";

interface AttentionPanelProps {
  items: AttentionItem[];
  /** True while any source of items is still loading. */
  pending: boolean;
}

/**
 * "What needs my attention?" — the first thing on Home when the answer is
 * "something", and a single calm line when it is "nothing".
 *
 * Severity is carried by an icon *and* a label, never by colour alone; each
 * row names the page that can fix it. The panel is not an error surface: it
 * lists real states from real endpoints, so a merchant learns to trust it.
 */
export function AttentionPanel({ items, pending }: AttentionPanelProps) {
  if (items.length === 0) {
    return (
      <p
        className="flex items-center gap-2 rounded-md border bg-card px-4 py-3 text-sm text-muted-foreground"
        data-testid="attention-clear"
      >
        <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden="true" />
        {pending ? "Checking for anything that needs attention…" : "Nothing needs your attention right now."}
      </p>
    );
  }

  return (
    <ul className="divide-y rounded-md border bg-card" data-testid="attention-list">
      {items.map((item) => {
        const Icon = item.severity === "error" ? XCircle : AlertTriangle;
        return (
          <li
            key={item.id}
            className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:gap-3"
          >
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <Icon
                className={cn(
                  "mt-0.5 h-4 w-4 shrink-0",
                  item.severity === "error" ? "text-destructive" : "text-warning",
                )}
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">
                  <span className="sr-only">
                    {item.severity === "error" ? "Error: " : "Warning: "}
                  </span>
                  {item.title}
                </p>
                {item.detail && (
                  <p className="text-sm text-muted-foreground">{item.detail}</p>
                )}
              </div>
            </div>
            {/* Below the text on a phone, beside it from `sm` up. */}
            <Button variant="outline" size="sm" asChild className="ml-7 w-fit shrink-0 sm:ml-0">
              <Link href={item.href} aria-label={`${item.action}: ${item.title}`}>
                {item.action}
              </Link>
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
