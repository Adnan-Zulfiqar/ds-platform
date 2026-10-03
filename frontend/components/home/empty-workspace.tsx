"use client";

import { CheckCircle2, Circle } from "lucide-react";
import Link from "next/link";

import { ImportProductDialog } from "@/components/products/import-product-dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { ChannelSummary } from "./home-rules";

interface EmptyWorkspaceProps {
  channels: ChannelSummary[];
}

/**
 * Home for a workspace with nothing in it.
 *
 * A new merchant used to meet fourteen zeros. This is the same three steps
 * the product actually requires, in order, each with the real control that
 * performs it — the Integrations page and the existing import dialog. Step
 * state comes from the channel summaries, so a step ticks itself off the
 * moment the connection exists; nothing is assumed.
 */
export function EmptyWorkspace({ channels }: EmptyWorkspaceProps) {
  const shopifyDone = channels.some((c) => c.id === "shopify" && c.usable);
  const aliexpressDone = channels.some((c) => c.id === "aliexpress" && c.usable);

  const steps = [
    {
      id: "import",
      done: false,
      title: "Import your first product",
      detail:
        "Paste an AliExpress product ID or URL. No AliExpress login needed — it lands in Drafts for you to edit.",
      action: <ImportProductDialog />,
    },
    {
      id: "shopify",
      done: shopifyDone,
      title: "Connect your store",
      detail: "Required when you publish — Shopify (or another sales channel) is where listings go live.",
      action: (
        <Button variant={shopifyDone ? "outline" : "default"} size="sm" asChild>
          <Link href="/settings/integrations">{shopifyDone ? "Manage" : "Connect Shopify"}</Link>
        </Button>
      ),
    },
    {
      id: "aliexpress",
      done: aliexpressDone,
      title: "Connect AliExpress (orders & tracking)",
      detail: "Only when you place supplier orders or sync tracking — not required to import or edit.",
      action: (
        <Button variant={aliexpressDone ? "outline" : "default"} size="sm" asChild>
          <Link href="/settings/integrations">{aliexpressDone ? "Manage" : "Connect AliExpress"}</Link>
        </Button>
      ),
    },
  ];

  return (
    <section
      aria-labelledby="home-setup-heading"
      className="rounded-lg border bg-card p-5"
      data-testid="empty-workspace"
    >
      <h2 id="home-setup-heading" className="text-base font-semibold">
        Set up your workspace
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Import and edit first. Connect a store when you publish; connect AliExpress only for orders.
      </p>
      <ol className="mt-4 space-y-4">
        {steps.map((step, index) => {
          const Icon = step.done ? CheckCircle2 : Circle;
          return (
            <li key={step.id} className="flex flex-wrap items-start gap-3">
              <Icon
                className={cn(
                  "mt-0.5 h-5 w-5 shrink-0",
                  step.done ? "text-success" : "text-muted-foreground/60",
                )}
                aria-hidden="true"
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">
                  <span className="sr-only">
                    Step {index + 1}, {step.done ? "done" : "to do"}:{" "}
                  </span>
                  {step.title}
                </p>
                <p className="text-sm text-muted-foreground">{step.detail}</p>
              </div>
              <div className="shrink-0">{step.action}</div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
