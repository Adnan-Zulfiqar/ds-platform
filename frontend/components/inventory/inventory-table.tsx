"use client";

import { Warehouse } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime, formatMoney } from "@/lib/utils";
import { useInventory } from "@/services/inventory";

export function InventoryTable() {
  const [q, setQ] = useState("");
  const { data, isLoading, isError, refetch } = useInventory({
    page: 1,
    size: 50,
    q: q || undefined,
    sortBy: "updated_at",
    sortDir: "desc",
  });

  if (isError) {
    return (
      <ErrorState
        title="Could not load inventory"
        onRetry={() => void refetch()}
      />
    );
  }

  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <Input
        aria-label="Search inventory"
        placeholder="Search by title or external id…"
        value={q}
        onChange={(event) => setQ(event.target.value)}
        className="max-w-sm"
      />

      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Warehouse}
          title="No inventory yet"
          description="Import products, then sync stock from AliExpress."
        />
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead>Stock</TableHead>
                <TableHead>Sell price</TableHead>
                <TableHead>Cost</TableHead>
                <TableHead>Last synced</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((product) => (
                <TableRow key={product.id}>
                  <TableCell>
                    <div className="font-medium">{product.title}</div>
                    <div className="text-xs text-muted-foreground">
                      {product.externalId}
                    </div>
                  </TableCell>
                  <TableCell>{product.stockQuantity}</TableCell>
                  <TableCell>
                    {formatMoney(product.sellPrice, product.currency)}
                  </TableCell>
                  <TableCell>
                    {formatMoney(product.costPriceMin, product.currency)}
                  </TableCell>
                  <TableCell>
                    {formatDateTime(product.lastSyncedAt)}
                    {product.lastSyncError && (
                      <p className="text-xs text-destructive">
                        {product.lastSyncError}
                      </p>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{product.status}</Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
