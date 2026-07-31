"use client";

import { Store as StoreIcon } from "lucide-react";

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
import { formatDateTime } from "@/lib/utils";
import { useStores, type StoreStatus } from "@/services/stores";

function statusVariant(
  status: StoreStatus,
): "default" | "secondary" | "destructive" | "success" | "warning" | "outline" {
  switch (status) {
    case "connected":
      return "success";
    case "syncing":
      return "warning";
    case "error":
      return "destructive";
    case "disconnected":
      return "secondary";
    default:
      return "outline";
  }
}

export function StoreTable() {
  const { data, isLoading, isError, refetch } = useStores({
    page: 1,
    size: 50,
    sortBy: "created_at",
    sortDir: "desc",
  });

  if (isError) {
    return (
      <ErrorState title="Could not load stores" onRetry={() => void refetch()} />
    );
  }

  if (isLoading) {
    return <Skeleton className="h-48 w-full" />;
  }

  const stores = data?.items ?? [];
  if (stores.length === 0) {
    return (
      <EmptyState
        icon={StoreIcon}
        title="No stores yet"
        description="Add a sales channel to track sync health and per-store settings."
      />
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Platform</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Health</TableHead>
            <TableHead>Last sync</TableHead>
            <TableHead>Last activity</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {stores.map((store) => (
            <TableRow key={store.id}>
              <TableCell>
                <div className="font-medium">{store.name}</div>
                <div className="text-xs text-muted-foreground">{store.slug}</div>
              </TableCell>
              <TableCell className="capitalize">
                {store.platform.replaceAll("_", " ")}
              </TableCell>
              <TableCell>
                <Badge variant={statusVariant(store.status)}>{store.status}</Badge>
              </TableCell>
              <TableCell>{store.healthScore}</TableCell>
              <TableCell>{formatDateTime(store.lastSyncAt)}</TableCell>
              <TableCell>{formatDateTime(store.lastActivityAt)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
