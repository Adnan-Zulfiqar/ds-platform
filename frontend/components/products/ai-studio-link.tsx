"use client";

import { Sparkles } from "lucide-react";
import Link from "next/link";

import { useCanUseStudio } from "@/components/ai-studio/studio-access";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * Catalogue entry point to AI Studio (Phase 9 Stage 10, plan §25).
 *
 * Replaces "Optimize with AI", which called `POST /optimize` — an endpoint
 * that generates *and activates* in one step, skipping preview, approval and
 * the token. The row now only links to the review page, where nothing changes
 * without a confirmation. The backend route is kept; no primary screen calls
 * it. Hidden for roles the Studio API refuses.
 */
export function AiStudioLink({ productId, compact = false }: { productId: string; compact?: boolean }) {
  const { allowed } = useCanUseStudio();
  if (!allowed) return null;

  const href = `/ai-studio/products/${productId}`;
  if (!compact) {
    return (
      <Button asChild size="sm" variant="outline">
        <Link href={href}>
          <Sparkles className="mr-2 h-4 w-4" aria-hidden="true" />
          AI Studio
        </Link>
      </Button>
    );
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button asChild size="icon" variant="ghost" className="h-9 w-9">
          <Link href={href} aria-label="AI Studio">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
          </Link>
        </Button>
      </TooltipTrigger>
      <TooltipContent>Review AI proposals in AI Studio</TooltipContent>
    </Tooltip>
  );
}
