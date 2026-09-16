"use client";

import {
  Eye,
  FileEdit,
  MoreHorizontal,
  Package,
  Pencil,
  RefreshCw,
  SearchX,
  Store,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { MouseEvent, ReactNode } from "react";

import { CataloguePagination } from "@/components/catalogue/catalogue-pagination";
import { toListQuery, useCatalogueQuery } from "@/components/catalogue/catalogue-query";
import { CatalogueToolbar } from "@/components/catalogue/catalogue-toolbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { useDrafts } from "@/services/drafts";
import { useProducts } from "@/services/products";
import type { Product, ProductAIStatus, ProductStatus } from "@/types/api";

import { ImportProductDialog } from "./import-product-dialog";
import { OptimizeProductButton } from "./optimize-product-button";
import { ProductVersionHistorySheet } from "./product-version-history-sheet";

/**
 * The catalogue workspace shared by Drafts and Products (UX-L2D-04).
 *
 * One component, two projections: the backend decides membership — a
 * product with a `synced` listing is a Product, otherwise a Draft — and the
 * two pages differ only in wording, the primary action and where a row leads.
 *
 * List state (search, sort, page) lives in the URL through
 * `useCatalogueQuery`; the API is asked for exactly that page with the wire
 * names it reads. Search is server-side (`q`), sort is server-side
 * (`sort_by`/`sort_dir`), pages come from the server's `meta`. Nothing is
 * filtered on the client, so what the merchant sees is what the query means.
 *
 * Status is what the list response can vouch for. A Products row is
 * "Published" because the endpoint only returns products with a synced
 * listing; per-listing detail (which store, visible or not) needs one
 * listings request per product and belongs on the product page, not in a
 * table of twenty-five rows. A Drafts row shows the product's own status
 * mapped to words — usually "Draft", sometimes "Unavailable" when the
 * supplier listing disappeared, which is worth a merchant's eye.
 *
 * Below `md` the table becomes a card list: the same rows, the same
 * actions, no sideways scrolling to reach Edit.
 */

const STATUS_LABEL: Record<ProductStatus, string> = {
  draft: "Draft",
  active: "Active",
  archived: "Archived",
  unavailable: "Unavailable",
};

const STATUS_VARIANT: Record<ProductStatus, "default" | "secondary" | "outline" | "destructive"> = {
  active: "default",
  draft: "secondary",
  archived: "outline",
  unavailable: "destructive",
};

const AI_STATUS_VARIANT: Record<ProductAIStatus, "default" | "secondary" | "outline" | "destructive"> = {
  optimized: "default",
  not_optimized: "secondary",
  failed: "destructive",
};

const AI_STATUS_LABEL: Record<ProductAIStatus, string> = {
  optimized: "Optimized",
  not_optimized: "Not optimized",
  failed: "Failed",
};

function formatPrice(product: Product): string {
  const { costPriceMin, costPriceMax, currency } = product;
  if (!costPriceMin) return "—";
  const symbol = currency ? `${currency} ` : "";
  if (!costPriceMax || costPriceMin === costPriceMax) return `${symbol}${costPriceMin}`;
  return `${symbol}${costPriceMin} – ${costPriceMax}`;
}

export type ProductTableVariant = "drafts" | "products";

interface ProductTableProps {
  /** Workspace projection — drafts never mix into the published Products list. */
  variant?: ProductTableVariant;
}

const COPY = {
  drafts: {
    noun: "drafts",
    nouns: ["draft", "drafts"] as [string, string],
    rowTestId: "draft-row",
    cardTestId: "draft-card",
    titleTestId: "draft-title-link",
    action: "Edit",
    actionIcon: Pencil,
    href: (id: string) => `/drafts/${id}`,
    rowLabel: (title: string) => `Edit ${title}`,
  },
  products: {
    noun: "products",
    nouns: ["product", "products"] as [string, string],
    rowTestId: "product-row",
    cardTestId: "product-card",
    titleTestId: "product-title-link",
    action: "View",
    actionIcon: Eye,
    href: (id: string) => `/products/${id}`,
    rowLabel: (title: string) => `View ${title}`,
  },
} as const;

function Frame({ toolbar, children }: { toolbar: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-4">
      {toolbar}
      {children}
    </div>
  );
}

export function ProductTable({ variant = "products" }: ProductTableProps) {
  const router = useRouter();
  const state = useCatalogueQuery();
  const listQuery = toListQuery(state.query);
  const draftsQuery = useDrafts(listQuery);
  const productsQuery = useProducts(listQuery);
  const query = variant === "drafts" ? draftsQuery : productsQuery;
  const copy = COPY[variant];
  const { data, isPending, isError, error, refetch, isFetching, isPlaceholderData } = query;
  // "Busy" is a refetch while rows are already on screen — a new page, sort
  // or search — not the first load, which has its own skeleton.
  const busy = isFetching && !isPending && (isPlaceholderData || Boolean(data));

  const toolbar = (
    <CatalogueToolbar state={state} noun={copy.noun} totalItems={data?.meta.totalItems} busy={busy} />
  );

  if (isPending) {
    return (
      <Frame toolbar={toolbar}>
        <div
          className="space-y-2"
          aria-busy="true"
          aria-label={`Loading ${copy.noun}`}
          data-testid={variant === "drafts" ? "drafts-loading" : "products-loading"}
        >
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-14 w-full" />
          ))}
        </div>
      </Frame>
    );
  }

  if (isError) {
    return (
      <Frame toolbar={toolbar}>
        <ErrorState
          title={variant === "drafts" ? "Could not load drafts" : "Could not load products"}
          description={error instanceof Error ? error.message : "Please try again."}
          onRetry={() => void refetch()}
        />
      </Frame>
    );
  }

  if (data.meta.totalItems === 0) {
    if (state.query.q) {
      // A search with no hits is not an empty catalogue.
      return (
        <Frame toolbar={toolbar}>
          <div data-testid="catalogue-no-results">
            <EmptyState
              icon={SearchX}
              title={`No ${copy.noun} match “${state.query.q}”`}
              description="Search looks at the title, supplier name and AliExpress ID. Try a shorter word."
              action={
                <Button variant="outline" onClick={() => state.update({ q: "" }, { replace: true })}>
                  Clear search
                </Button>
              }
            />
          </div>
        </Frame>
      );
    }
    return (
      <Frame toolbar={toolbar}>
        <div data-testid="catalogue-empty">
          {variant === "drafts" ? (
            <EmptyState
              icon={FileEdit}
              title="No drafts yet"
              description="Import as Draft from AliExpress to review pricing, media, and variants before publishing to a store."
              action={<ImportProductDialog />}
            />
          ) : (
            <EmptyState
              icon={Package}
              title="No published products yet"
              description="Products appear here after a successful Publish to Store. Imported drafts stay under Drafts until then."
              action={
                <Button asChild>
                  <Link href="/drafts">Go to Drafts</Link>
                </Button>
              }
            />
          )}
        </div>
      </Frame>
    );
  }

  if (data.items.length === 0) {
    // A page beyond the end — a stale link after deletions. The rows are on
    // the server's last page, so offer that rather than a blank table.
    return (
      <Frame toolbar={toolbar}>
        <div data-testid="catalogue-page-out-of-range">
          <EmptyState
            icon={SearchX}
            title="This page is empty"
            description={`There are ${data.meta.totalPages} pages of ${copy.noun}.`}
            action={
              <Button variant="outline" onClick={() => state.update({ page: data.meta.totalPages })}>
                Go to the last page
              </Button>
            }
          />
        </div>
      </Frame>
    );
  }

  const ActionIcon = copy.actionIcon;

  const navigateOnRowClick = (href: string) => (event: MouseEvent<HTMLElement>) => {
    // The row is a convenience target. Real links and controls inside it keep
    // their own behaviour (middle-click, modifier keys, menus).
    const target = event.target as HTMLElement;
    if (target.closest("a, button, [role='menu'], [data-row-actions]")) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    router.push(href);
  };

  const rowActions = (product: Product, layout: "table" | "card") => {
    const href = copy.href(product.id);
    return (
      <div
        className={cn("flex items-center gap-1", layout === "table" ? "justify-end" : "flex-wrap")}
        data-row-actions
      >
        <Button variant="outline" size="sm" asChild>
          <Link href={href} aria-label={copy.rowLabel(product.title)}>
            <ActionIcon className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            {copy.action}
          </Link>
        </Button>
        <OptimizeProductButton productId={product.id} compact />
        <ProductVersionHistorySheet productId={product.id} productTitle={product.title} compact />
        {variant === "drafts" && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-9 w-9"
                aria-label={`More actions for ${product.title}`}
              >
                <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link href={`${href}?tab=overview`}>
                  <Eye className="mr-2 h-4 w-4" aria-hidden="true" />
                  Preview
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href={`${href}?tab=overview`}>
                  <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />
                  Refresh supplier data
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild>
                <Link href={`${href}?tab=publishing`}>
                  <Store className="mr-2 h-4 w-4" aria-hidden="true" />
                  Publish to store
                </Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    );
  };

  const statusBadge = (product: Product) =>
    variant === "products" ? (
      <Badge variant="default" data-testid="product-listing-status">
        Published
      </Badge>
    ) : (
      <Badge variant={STATUS_VARIANT[product.status]} data-testid="draft-status">
        {STATUS_LABEL[product.status]}
      </Badge>
    );

  const aiBadge = (product: Product) => (
    <Badge variant={AI_STATUS_VARIANT[product.aiStatus]} data-testid="ai-status-badge">
      {AI_STATUS_LABEL[product.aiStatus]}
    </Badge>
  );

  const placeholder = (
    // No image field on the list row yet — a truthful placeholder, not a
    // guessed supplier URL (UX-L2D-GATE-04: thumbnails deferred).
    <span
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border bg-muted/40 text-muted-foreground"
      aria-hidden="true"
    >
      <Package className="h-4 w-4" />
    </span>
  );

  return (
    <div className="space-y-4" data-testid="catalogue" aria-busy={busy ? "true" : undefined}>
      {toolbar}

      {/* Desktop: the table. Hidden (not merely shrunk) below `md`, where
          nine columns cannot fit and the card list takes over. */}
      <div
        className={cn(
          // Table from `lg`, cards below (UX-L2D-07 moved this up from `md`):
          // at 768 the expanded sidebar leaves ~490px, and a six-column table
          // put the primary action behind a sideways scroll.
          "hidden overflow-x-auto rounded-md border lg:block",
          busy && "opacity-70 transition-opacity",
        )}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-14">
                <span className="sr-only">Image</span>
              </TableHead>
              <TableHead>Product</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Supplier price</TableHead>
              <TableHead className="text-right">Stock</TableHead>
              <TableHead className="text-right">Variants</TableHead>
              <TableHead>Supplier</TableHead>
              <TableHead>AI status</TableHead>
              <TableHead className="text-right">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.items.map((product) => {
              const href = copy.href(product.id);
              return (
                <TableRow
                  key={product.id}
                  data-testid={copy.rowTestId}
                  className="cursor-pointer hover:bg-muted/40"
                  onClick={navigateOnRowClick(href)}
                >
                  <TableCell>{placeholder}</TableCell>
                  <TableCell className="max-w-md">
                    <Link
                      href={href}
                      className="line-clamp-2 font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      data-testid={copy.titleTestId}
                    >
                      {product.title}
                    </Link>
                    <span className="block text-xs text-muted-foreground">{product.externalId}</span>
                  </TableCell>
                  <TableCell>{statusBadge(product)}</TableCell>
                  <TableCell className="whitespace-nowrap">{formatPrice(product)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {product.stockQuantity.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums" data-testid="variant-count">
                    {product.variantCount ?? "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{product.supplierName ?? "—"}</TableCell>
                  <TableCell>{aiBadge(product)}</TableCell>
                  <TableCell className="text-right">{rowActions(product, "table")}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Phone: one card per row, the primary action always on screen. Both
          representations are in the DOM (CSS decides which one shows), so
          the card carries its own test identity: a `*-row` is always the
          table row, a `*-card` always the card. */}
      <ul
        className={cn("space-y-3 lg:hidden", busy && "opacity-70 transition-opacity")}
        data-testid="catalogue-cards"
      >
        {data.items.map((product) => {
          const href = copy.href(product.id);
          return (
            <li
              key={product.id}
              data-testid={copy.cardTestId}
              className="rounded-lg border bg-card p-4"
              onClick={navigateOnRowClick(href)}
            >
              <div className="flex items-start gap-3">
                {placeholder}
                <div className="min-w-0 flex-1">
                  <Link
                    href={href}
                    className="line-clamp-2 font-medium underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    data-testid={copy.titleTestId}
                  >
                    {product.title}
                  </Link>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {product.supplierName ?? "Supplier unknown"} · {formatPrice(product)} · stock{" "}
                    {product.stockQuantity.toLocaleString()} · {product.variantCount ?? "—"} variants
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {statusBadge(product)}
                    {aiBadge(product)}
                  </div>
                </div>
              </div>
              <div className="mt-3">{rowActions(product, "card")}</div>
            </li>
          );
        })}
      </ul>

      <CataloguePagination
        meta={data.meta}
        busy={busy}
        noun={copy.nouns}
        onPage={(page) => state.update({ page })}
      />
    </div>
  );
}
