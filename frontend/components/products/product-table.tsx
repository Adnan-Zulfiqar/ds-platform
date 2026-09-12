"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Eye,
  FileEdit,
  MoreHorizontal,
  Package,
  Pencil,
  RefreshCw,
  Store,
} from "lucide-react";

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
import { useDrafts } from "@/services/drafts";
import { useProducts } from "@/services/products";
import type { Product, ProductAIStatus, ProductStatus } from "@/types/api";

import { ImportProductDialog } from "./import-product-dialog";
import { OptimizeProductButton } from "./optimize-product-button";
import { ProductVersionHistorySheet } from "./product-version-history-sheet";

const STATUS_VARIANT: Record<
  ProductStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  active: "default",
  draft: "secondary",
  archived: "outline",
  unavailable: "destructive",
};

const AI_STATUS_VARIANT: Record<
  ProductAIStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
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
  if (!costPriceMax || costPriceMin === costPriceMax) {
    return `${symbol}${costPriceMin}`;
  }
  return `${symbol}${costPriceMin} – ${costPriceMax}`;
}

export type ProductTableVariant = "drafts" | "products";

interface ProductTableProps {
  /** Workspace projection — drafts never mix into the published Products list. */
  variant?: ProductTableVariant;
}

export function ProductTable({ variant = "products" }: ProductTableProps) {
  const router = useRouter();
  const draftsQuery = useDrafts({ size: 25 });
  const productsQuery = useProducts({ size: 25 });
  const { data, isPending, isError, error, refetch } =
    variant === "drafts" ? draftsQuery : productsQuery;

  if (isPending) {
    return (
      <div
        className="space-y-2"
        data-testid={
          variant === "drafts" ? "drafts-loading" : "products-loading"
        }
      >
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title={
          variant === "drafts"
            ? "Could not load drafts"
            : "Could not load products"
        }
        description={
          error instanceof Error ? error.message : "Please try again."
        }
        onRetry={() => void refetch()}
      />
    );
  }

  if (data.items.length === 0) {
    if (variant === "drafts") {
      return (
        <EmptyState
          icon={FileEdit}
          title="No drafts yet"
          description="Import as Draft from AliExpress to review pricing, media, and variants before publishing to a store."
          action={<ImportProductDialog />}
        />
      );
    }

    return (
      <EmptyState
        icon={Package}
        title="No published products yet"
        description="Products appear here after a successful Publish to Store. Imported drafts live under Drafts until then."
        action={
          <Link
            href="/drafts"
            className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
          >
            Go to Drafts
          </Link>
        }
      />
    );
  }

  const rowTestId = variant === "drafts" ? "draft-row" : "product-row";
  const editorHref = (id: string) =>
    variant === "drafts" ? `/drafts/${id}` : `/products/${id}`;

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-14">Image</TableHead>
            <TableHead>Product</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Supplier price</TableHead>
            <TableHead className="text-right">Stock</TableHead>
            <TableHead className="text-right">Variants</TableHead>
            <TableHead>Supplier</TableHead>
            <TableHead>AI status</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((product) => {
            const href = editorHref(product.id);
            return (
              <TableRow
                key={product.id}
                data-testid={rowTestId}
                className="cursor-pointer hover:bg-muted/40"
                onClick={() => {
                  router.push(href);
                }}
              >
                <TableCell>
                  <Link
                    href={href}
                    onClick={(event) => event.stopPropagation()}
                    className="flex h-10 w-10 items-center justify-center rounded-md border bg-muted/40 text-muted-foreground"
                    aria-label={
                      variant === "drafts"
                        ? `Edit ${product.title}`
                        : `View ${product.title}`
                    }
                  >
                    <Package className="h-4 w-4" aria-hidden="true" />
                  </Link>
                </TableCell>
                <TableCell className="max-w-md">
                  <Link
                    href={href}
                    onClick={(event) => event.stopPropagation()}
                    className="line-clamp-2 font-medium text-foreground underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    data-testid={
                      variant === "drafts" ? "draft-title-link" : "product-title-link"
                    }
                  >
                    {product.title}
                  </Link>
                  <span className="block text-xs text-muted-foreground">
                    {product.externalId}
                  </span>
                </TableCell>
                <TableCell>
                  <Badge
                    variant={
                      variant === "products" ? "default" : STATUS_VARIANT[product.status]
                    }
                    data-testid={
                      variant === "products" ? "product-listing-status" : undefined
                    }
                  >
                    {variant === "products" ? "Added to Shopify" : product.status}
                  </Badge>
                </TableCell>
                <TableCell>{formatPrice(product)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {product.stockQuantity.toLocaleString()}
                </TableCell>
                <TableCell
                  className="text-right tabular-nums"
                  data-testid="variant-count"
                >
                  {/* Always a real number from the server (see the `Product`
                      type's doc comment) — the `??` is defensive against a
                      stale cached response shape, not a real "unknown" case;
                      never fabricate a count in its place. */}
                  {product.variantCount ?? "—"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {product.supplierName ?? "—"}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={AI_STATUS_VARIANT[product.aiStatus]}
                    data-testid="ai-status-badge"
                  >
                    {AI_STATUS_LABEL[product.aiStatus]}
                  </Badge>
                </TableCell>
                <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                  <div className="flex items-center justify-end gap-1">
                    {variant === "drafts" ? (
                      <>
                        <Button variant="outline" size="sm" asChild>
                          <Link href={href}>
                            <Pencil className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                            Edit Draft
                          </Link>
                        </Button>
                        <OptimizeProductButton productId={product.id} />
                        <ProductVersionHistorySheet
                          productId={product.id}
                          productTitle={product.title}
                        />
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label="More draft actions"
                            >
                              <MoreHorizontal className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem asChild>
                              <Link href={`${href}?tab=overview`}>
                                <Eye className="mr-2 h-4 w-4" />
                                Preview
                              </Link>
                            </DropdownMenuItem>
                            <DropdownMenuItem asChild>
                              <Link href={`${href}?tab=overview`}>
                                <RefreshCw className="mr-2 h-4 w-4" />
                                Refresh Supplier Data
                              </Link>
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem asChild>
                              <Link href={`${href}?tab=publishing`}>
                                <Store className="mr-2 h-4 w-4" />
                                Publish to Store
                              </Link>
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </>
                    ) : (
                      <>
                        <OptimizeProductButton productId={product.id} />
                        <ProductVersionHistorySheet
                          productId={product.id}
                          productTitle={product.title}
                        />
                      </>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
