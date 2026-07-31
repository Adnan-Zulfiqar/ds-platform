"use client";

import { Package } from "lucide-react";

import { Badge } from "@/components/ui/badge";
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
import { useProducts } from "@/services/products";
import type { Product, ProductStatus } from "@/types/api";

import { ImportProductDialog } from "./import-product-dialog";

const STATUS_VARIANT: Record<
  ProductStatus,
  "default" | "secondary" | "outline" | "destructive"
> = {
  active: "default",
  draft: "secondary",
  archived: "outline",
  unavailable: "destructive",
};

/**
 * Format a supplier price for display.
 *
 * The value arrives as a string because the backend stores it as `Decimal`.
 * It is **not** parsed into a number: doing so would reintroduce the binary
 * floating-point error the backend avoided. A range collapses to one figure
 * when both ends agree, which is the common case for a single-variant product.
 */
function formatPrice(product: Product): string {
  const { costPriceMin, costPriceMax, currency } = product;
  if (!costPriceMin) return "—";

  const symbol = currency ? `${currency} ` : "";
  if (!costPriceMax || costPriceMin === costPriceMax) {
    return `${symbol}${costPriceMin}`;
  }
  return `${symbol}${costPriceMin} – ${costPriceMax}`;
}

export function ProductTable() {
  const { data, isPending, isError, error, refetch } = useProducts({ size: 25 });

  // Loading, error and empty are three distinct states, deliberately not
  // collapsed. Showing "no products" when the request failed is how a user is
  // told their catalogue is empty when it is not.
  if (isPending) {
    return (
      <div className="space-y-2" data-testid="products-loading">
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Could not load products"
        description={
          error instanceof Error ? error.message : "Please try again."
        }
        onRetry={() => void refetch()}
      />
    );
  }

  if (data.items.length === 0) {
    return (
      <EmptyState
        icon={Package}
        title="No products yet"
        description="Import a product from AliExpress to start building your catalogue."
        action={<ImportProductDialog />}
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Product</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Supplier price</TableHead>
            <TableHead className="text-right">Stock</TableHead>
            <TableHead>Supplier</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.map((product) => (
            <TableRow key={product.id} data-testid="product-row">
              <TableCell className="max-w-md">
                <span className="line-clamp-2 font-medium">
                  {product.title}
                </span>
                <span className="text-xs text-muted-foreground">
                  {product.externalId}
                </span>
              </TableCell>
              <TableCell>
                <Badge variant={STATUS_VARIANT[product.status]}>
                  {product.status}
                </Badge>
              </TableCell>
              <TableCell>{formatPrice(product)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {product.stockQuantity.toLocaleString()}
              </TableCell>
              <TableCell className="text-muted-foreground">
                {product.supplierName ?? "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
