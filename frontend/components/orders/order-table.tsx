"use client";

import { ChevronLeft, ChevronRight, Search, ShoppingCart } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { FulfillmentStatusBadge } from "@/components/orders/order-status-badge";
import { SyncOrdersDialog } from "@/components/orders/sync-orders-dialog";
import { Button } from "@/components/ui/button";
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
import { useOrders } from "@/services/orders";
import type { FulfillmentStatus } from "@/types/api";

const PAGE_SIZE = 25;

const STATUS_OPTIONS: Array<{ value: FulfillmentStatus | ""; label: string }> = [
  { value: "", label: "All statuses" },
  { value: "pending", label: "Pending" },
  { value: "awaiting_payment", label: "Awaiting payment" },
  { value: "paid", label: "Paid" },
  { value: "processing", label: "Processing" },
  { value: "fulfilled", label: "Fulfilled" },
  { value: "shipped", label: "Shipped" },
  { value: "delivered", label: "Delivered" },
  { value: "cancelled", label: "Cancelled" },
  { value: "refunded", label: "Refunded" },
  { value: "disputed", label: "Disputed" },
];

const WINDOW_OPTIONS = [
  { value: "", label: "All time" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
];

const SORT_OPTIONS = [
  { value: "external_created_at:desc", label: "Newest first" },
  { value: "external_created_at:asc", label: "Oldest first" },
  { value: "total_amount:desc", label: "Highest total" },
  { value: "total_amount:asc", label: "Lowest total" },
];

/**
 * Native `<select>` styled to match the Input primitive.
 *
 * There is deliberately no custom Select component in the design system yet,
 * and building one for two dropdowns would violate the no-abstraction-without-
 * a-second-caller rule harder than a styled native element does. The native
 * control is also the accessible baseline on mobile.
 */
function FilterSelect({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  label: string;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-9 rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function OrderTable() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [windowDays, setWindowDays] = useState("");
  const [sort, setSort] = useState("external_created_at:desc");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");

  const [sortBy, sortDir] = sort.split(":") as [string, "asc" | "desc"];
  const dateFrom = windowDays
    ? new Date(Date.now() - Number(windowDays) * 86_400_000).toISOString()
    : undefined;

  const { data, isPending, isError, error, refetch } = useOrders({
    page,
    size: PAGE_SIZE,
    q: search || undefined,
    status: (status || undefined) as FulfillmentStatus | undefined,
    dateFrom,
    sortBy,
    sortDir,
  });

  function applyFilter(update: () => void) {
    update();
    setPage(1);
  }

  const filters = (
    <div className="flex flex-wrap items-center gap-2" data-testid="order-filters">
      <form
        className="relative"
        onSubmit={(event) => {
          event.preventDefault();
          applyFilter(() => setSearch(searchInput.trim()));
        }}
      >
        <Search
          className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          type="search"
          placeholder="Search orders…"
          aria-label="Search orders"
          className="h-9 w-56 pl-8"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
        />
      </form>
      <FilterSelect
        label="Filter by status"
        value={status}
        onChange={(value) => applyFilter(() => setStatus(value))}
        options={STATUS_OPTIONS as Array<{ value: string; label: string }>}
      />
      <FilterSelect
        label="Filter by date"
        value={windowDays}
        onChange={(value) => applyFilter(() => setWindowDays(value))}
        options={WINDOW_OPTIONS}
      />
      <FilterSelect
        label="Sort orders"
        value={sort}
        onChange={(value) => applyFilter(() => setSort(value))}
        options={SORT_OPTIONS}
      />
    </div>
  );

  if (isPending) {
    return (
      <div className="space-y-4">
        {filters}
        <div className="space-y-2" data-testid="orders-loading">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-14 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Could not load orders"
        description={error instanceof Error ? error.message : "Please try again."}
        onRetry={() => void refetch()}
      />
    );
  }

  const hasFilter = Boolean(search || status || windowDays);

  if (data.items.length === 0 && !hasFilter) {
    return (
      <EmptyState
        icon={ShoppingCart}
        title="No orders yet"
        description="Orders appear here after your first synchronisation with AliExpress."
        action={<SyncOrdersDialog />}
      />
    );
  }

  return (
    <div className="space-y-4">
      {filters}

      {data.items.length === 0 ? (
        <EmptyState
          icon={ShoppingCart}
          title="No matching orders"
          description="No orders match the current filters. Clear them to see everything."
        />
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Order</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Buyer</TableHead>
                <TableHead className="text-right">Items</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead>Placed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((order) => (
                <TableRow key={order.id} data-testid="order-row">
                  <TableCell>
                    <Link
                      href={`/orders/${order.id}`}
                      className="font-medium hover:underline"
                    >
                      {order.externalId}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {order.source}
                      {order.countryCode ? ` · ${order.countryCode}` : ""}
                    </p>
                  </TableCell>
                  <TableCell>
                    <FulfillmentStatusBadge status={order.fulfillmentStatus} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {order.buyerName ?? "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {order.itemCount}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(order.totalAmount, order.currency)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatDateTime(order.externalCreatedAt)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          Page {data.meta.page} of {Math.max(data.meta.totalPages, 1)} ·{" "}
          {data.meta.totalItems} order{data.meta.totalItems === 1 ? "" : "s"}
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!data.meta.hasPrevious}
            onClick={() => setPage((current) => current - 1)}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!data.meta.hasNext}
            onClick={() => setPage((current) => current + 1)}
          >
            Next
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>
    </div>
  );
}
