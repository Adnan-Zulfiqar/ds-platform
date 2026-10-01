import type { Metadata } from "next";
import { Suspense } from "react";

import { StudioAccess } from "@/components/ai-studio/studio-access";
import { StudioHome } from "@/components/ai-studio/studio-home";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";

export const metadata: Metadata = { title: "AI Studio" };

/**
 * `/ai-studio` — AI Product Studio home (Phase 9 Stage 10): bulk selection
 * across Drafts and Published, and the run dashboard (`?run=`).
 */
export default function AiStudioPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Studio"
        description="Generate AI proposals for your products and review each one before anything is approved or published."
      />
      <StudioAccess>
        <Suspense fallback={<Skeleton className="h-64 w-full" />}>
          <StudioHome />
        </Suspense>
      </StudioAccess>
    </div>
  );
}
