"use client";

import { MoreHorizontal } from "lucide-react";
import Link from "next/link";

import { useCanUseStudio } from "@/components/ai-studio/studio-access";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface ProductActionsMenuProps {
  refreshing: boolean;
  hasExternalUrl: boolean;
  onRefresh: () => void;
  /** AI Studio review route. Phase 9 Stage 10 replaced "Improve with AI
   * tools", which generated and activated AI text in one click, with this
   * link: nothing changes in the Studio without a confirmation. */
  aiStudioHref: string;
  onOpenAliExpress: () => void;
  onViewHistory: () => void;
}

/**
 * Tertiary actions — keeps the identity row focused on Preview / Save / Publish.
 *
 * Duplicate / Archive / Delete are intentionally omitted until their APIs exist.
 * Showing unusable disabled rows that do nothing is worse than hiding them.
 */
export function ProductActionsMenu({
  refreshing,
  hasExternalUrl,
  onRefresh,
  aiStudioHref,
  onOpenAliExpress,
  onViewHistory,
}: ProductActionsMenuProps) {
  const { allowed: canUseStudio } = useCanUseStudio();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          aria-label="More actions"
          data-testid="product-actions-menu"
        >
          <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
          <span className="ml-1.5 hidden sm:inline">More</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuItem
          disabled={refreshing}
          onSelect={() => onRefresh()}
        >
          {refreshing ? "Refreshing…" : "Refresh supplier information"}
        </DropdownMenuItem>
        {canUseStudio ? (
          <DropdownMenuItem asChild>
            <Link href={aiStudioHref}>Open AI Studio</Link>
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem
          disabled={!hasExternalUrl}
          onSelect={() => onOpenAliExpress()}
        >
          Open supplier product
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onViewHistory()}>
          Version history
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
