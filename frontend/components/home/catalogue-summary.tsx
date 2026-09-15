"use client";

import { FileEdit, Package, ShoppingCart, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { Skeleton } from "@/components/ui/skeleton";

interface SummaryTile {
  id: string;
  label: string;
  value: number | undefined;
  href: string;
  icon: LucideIcon;
  /** What clicking does — read to assistive technology with the number. */
  hint: string;
}

interface CatalogueSummaryProps {
  drafts: number | undefined;
  products: number | undefined;
  /** `undefined` while loading; `null` when order statistics are unavailable. */
  ordersAwaitingFulfilment: number | null | undefined;
}

/**
 * Three numbers a merchant acts on, each a link to the list behind it.
 *
 * Drafts and Products come from `GET /products/workspace-counts` — the same
 * source as the sidebar badges, so the two never disagree. Orders awaiting
 * fulfilment comes from `GET /orders/statistics`. Nothing monetary: the
 * analytics revenue figure sums orders in mixed currencies and carries no
 * currency of its own, so Home shows no money at all rather than a wrong
 * label (UX-L2D-01 F-6).
 */
export function CatalogueSummary({ drafts, products, ordersAwaitingFulfilment }: CatalogueSummaryProps) {
  const tiles: SummaryTile[] = [
    { id: "drafts", label: "Drafts", value: drafts, href: "/drafts", icon: FileEdit, hint: "open Drafts" },
    { id: "products", label: "Products", value: products, href: "/products", icon: Package, hint: "open Products" },
  ];
  if (ordersAwaitingFulfilment !== null) {
    tiles.push({
      id: "orders",
      label: "Orders awaiting fulfilment",
      value: ordersAwaitingFulfilment,
      href: "/orders",
      icon: ShoppingCart,
      hint: "open Orders",
    });
  }

  return (
    <ul className="grid gap-3 sm:grid-cols-3" data-testid="catalogue-summary">
      {tiles.map((tile) => {
        const Icon = tile.icon;
        return (
          <li key={tile.id}>
            <Link
              href={tile.href}
              className="flex h-full flex-col gap-2 rounded-lg border bg-card p-4 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={
                tile.value === undefined
                  ? `${tile.label}, loading — ${tile.hint}`
                  : `${tile.value} ${tile.label} — ${tile.hint}`
              }
              data-testid={`summary-${tile.id}`}
            >
              <span className="flex items-center justify-between gap-2">
                {tile.value === undefined ? (
                  <Skeleton className="h-7 w-10" />
                ) : (
                  <span className="text-2xl font-semibold tabular-nums leading-none">
                    {tile.value.toLocaleString()}
                  </span>
                )}
                <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              </span>
              {/* Wraps rather than truncates: a label cut to "Ord…" says nothing. */}
              <span className="text-xs leading-tight text-muted-foreground">{tile.label}</span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
