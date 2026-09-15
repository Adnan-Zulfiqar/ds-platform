"use client";

import { Pencil } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Product, ProductAIStatus } from "@/types/api";

import { formatRelativeTime } from "./home-rules";

interface RecentDraftsProps {
  drafts: Product[];
}

const AI_LABEL: Record<ProductAIStatus, string> = {
  optimized: "AI optimised",
  not_optimized: "Not optimised",
  failed: "AI failed",
};

const AI_VARIANT: Record<ProductAIStatus, "secondary" | "success" | "destructive"> = {
  optimized: "success",
  not_optimized: "secondary",
  failed: "destructive",
};

/**
 * "What am I working on?" — the five most recently edited drafts.
 *
 * A continuation surface, not a second Drafts page: title, where it came from,
 * when it was last touched, its AI state, and one action. The list row has no
 * image field (a backend dependency recorded in UX-L2D-01), so none is faked.
 */
export function RecentDrafts({ drafts }: RecentDraftsProps) {
  if (drafts.length === 0) {
    return (
      <p className="rounded-md border bg-card px-4 py-3 text-sm text-muted-foreground" data-testid="recent-drafts-empty">
        No drafts yet. Imported products appear here for you to finish.
      </p>
    );
  }

  return (
    <ul className="divide-y rounded-md border bg-card" data-testid="recent-drafts">
      {drafts.map((draft) => (
        <li key={draft.id} className="flex items-center gap-3 px-4 py-3">
          <div className="min-w-0 flex-1">
            <Link
              href={`/drafts/${draft.id}`}
              className="line-clamp-1 text-sm font-medium underline-offset-4 hover:underline"
            >
              {draft.title}
            </Link>
            <p className="text-xs text-muted-foreground">
              {draft.source === "aliexpress" ? "AliExpress" : "Manual"}
              {draft.supplierName && draft.source !== "aliexpress" ? ` · ${draft.supplierName}` : ""}
              {" · updated "}
              <time dateTime={draft.updatedAt}>{formatRelativeTime(draft.updatedAt)}</time>
            </p>
          </div>
          <Badge variant={AI_VARIANT[draft.aiStatus]} className="hidden shrink-0 sm:inline-flex">
            {AI_LABEL[draft.aiStatus]}
          </Badge>
          <Button variant="outline" size="sm" asChild className="shrink-0">
            <Link href={`/drafts/${draft.id}`} aria-label={`Edit ${draft.title}`}>
              <Pencil className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              Edit
            </Link>
          </Button>
        </li>
      ))}
    </ul>
  );
}
