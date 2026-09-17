"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import type { NextStep } from "./home-rules";

interface NextStepCardProps {
  step: NextStep | null;
}

/**
 * The one thing to do next, decided by the ordered rules in `home-rules.ts`.
 *
 * Deliberately not called a recommendation: nothing here is inferred or
 * generated, it is the first rule that applies to the workspace's real state.
 * While the inputs load the card keeps its size so the page does not jump.
 */
export function NextStepCard({ step }: NextStepCardProps) {
  return (
    <div
      className="rounded-lg border border-primary/30 bg-primary/5 p-4"
      data-testid="next-step"
      data-rule={step?.rule ?? "pending"}
    >
      <p className="text-xs font-semibold uppercase tracking-wider text-primary">Next step</p>
      {step ? (
        <div className="mt-1 flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-base font-semibold">{step.title}</h3>
            <p className="text-sm text-muted-foreground">{step.detail}</p>
          </div>
          <Button asChild>
            <Link href={step.href}>
              {step.action}
              <ArrowRight className="ml-1.5 h-4 w-4" aria-hidden="true" />
            </Link>
          </Button>
        </div>
      ) : (
        <div className="mt-2 space-y-2" aria-busy="true" aria-label="Loading next step">
          <Skeleton className="h-5 w-64 max-w-full" />
          <Skeleton className="h-4 w-80 max-w-full" />
        </div>
      )}
    </div>
  );
}
