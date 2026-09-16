"use client";

import { MoreHorizontal } from "lucide-react";

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
  optimizing: boolean;
  hasExternalUrl: boolean;
  onRefresh: () => void;
  onOptimize: () => void;
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
  optimizing,
  hasExternalUrl,
  onRefresh,
  onOptimize,
  onOpenAliExpress,
  onViewHistory,
}: ProductActionsMenuProps) {
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
        <DropdownMenuItem
          disabled={optimizing}
          onSelect={() => onOptimize()}
        >
          {optimizing ? "Optimizing…" : "Improve with AI tools"}
        </DropdownMenuItem>
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
