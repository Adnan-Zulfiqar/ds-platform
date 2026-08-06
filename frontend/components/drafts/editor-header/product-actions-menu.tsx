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
  onGoHistoryTab: () => void;
}

/**
 * Tertiary actions — keeps the identity row focused on Preview / Save / Publish.
 *
 * Duplicate/Archive/Delete remain listed but disabled until those APIs exist,
 * so the menu hierarchy matches the product brief without inventing behaviour.
 */
export function ProductActionsMenu({
  refreshing,
  optimizing,
  hasExternalUrl,
  onRefresh,
  onOptimize,
  onOpenAliExpress,
  onViewHistory,
  onGoHistoryTab,
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
          {refreshing ? "Refreshing…" : "Refresh Supplier Data"}
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={optimizing}
          onSelect={() => onOptimize()}
        >
          {optimizing ? "Optimizing…" : "Optimize with AI"}
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!hasExternalUrl}
          onSelect={() => onOpenAliExpress()}
        >
          Open AliExpress Listing
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onViewHistory()}>
          View History
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onGoHistoryTab()}>
          Open History tab
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled>Duplicate Draft</DropdownMenuItem>
        <DropdownMenuItem disabled>Archive Draft</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled
          className="text-destructive focus:text-destructive"
        >
          Delete Draft
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
