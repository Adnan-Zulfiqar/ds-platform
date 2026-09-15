"use client";

import Link from "next/link";
import { Store as StoreIcon } from "lucide-react";

import { ChannelStatusBadge } from "@/components/integrations/channel-status-badge";
import { Button } from "@/components/ui/button";
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
import { deriveStoreRecordState, PLATFORM_LABEL } from "@/lib/channel-state";
import { formatDateTime } from "@/lib/utils";
import { useStores, type Store } from "@/services/stores";

/**
 * Store records as a table from `md`, a card list below it.
 *
 * Status words come from the shared channel vocabulary, never the raw enum;
 * the old "Health" column is gone because `healthScore` is a counter the
 * backend nudges up and down on sync outcomes, with no meaning a merchant
 * could act on. Rows do not carry actions of their own: authorization is
 * managed under Integrations, and the row says so.
 */
export function StoreTable() {
  const { data, isLoading, isError, refetch } = useStores({
    page: 1,
    size: 50,
    sortBy: "created_at",
    sortDir: "desc",
  });

  if (isError) {
    return <ErrorState title="Could not load stores" onRetry={() => void refetch()} />;
  }

  if (isLoading) {
    return <Skeleton className="h-48 w-full" data-testid="stores-loading" />;
  }

  const stores = data?.items ?? [];
  if (stores.length === 0) {
    return (
      <div data-testid="stores-empty">
        <EmptyState
          icon={StoreIcon}
          title="No stores yet"
          description="Connect a Shopify store under Integrations. It appears here once authorized."
          action={
            <Button asChild>
              <Link href="/settings/integrations">Go to Integrations</Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div data-testid="stores" className="space-y-4">
      <div className="hidden overflow-x-auto rounded-md border md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Store</TableHead>
              <TableHead>Platform</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Last sync</TableHead>
              <TableHead>Last activity</TableHead>
              <TableHead>
                <span className="sr-only">Manage</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {stores.map((store) => {
              const state = deriveStoreRecordState(store);
              return (
                <TableRow key={store.id} data-testid="store-row">
                  <TableCell>
                    <div className="font-medium">{store.name}</div>
                    <div className="max-w-[28ch] truncate text-xs text-muted-foreground" title={store.storefrontUrl ?? store.slug}>
                      {store.storefrontUrl?.replace(/^https?:\/\//, "") ?? store.slug}
                    </div>
                  </TableCell>
                  <TableCell>{PLATFORM_LABEL[store.platform] ?? store.platform}</TableCell>
                  <TableCell>
                    <ChannelStatusBadge state={state} data-testid="store-status" />
                    <p className="mt-1 max-w-[36ch] text-xs text-muted-foreground">{state.detail}</p>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{formatDateTime(store.lastSyncAt)}</TableCell>
                  <TableCell className="whitespace-nowrap">{formatDateTime(store.lastActivityAt)}</TableCell>
                  <TableCell className="text-right">
                    <ManageLink store={store} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <ul className="space-y-3 md:hidden" data-testid="store-cards">
        {stores.map((store) => {
          const state = deriveStoreRecordState(store);
          return (
            <li key={store.id} className="rounded-md border p-3" data-testid="store-card">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium">{store.name}</p>
                  <p className="break-all text-xs text-muted-foreground">
                    {PLATFORM_LABEL[store.platform] ?? store.platform} ·{" "}
                    {store.storefrontUrl?.replace(/^https?:\/\//, "") ?? store.slug}
                  </p>
                </div>
                <ChannelStatusBadge state={state} />
              </div>
              <p className="mt-2 text-sm text-muted-foreground">{state.detail}</p>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 text-xs">
                <dt className="text-muted-foreground">Last sync</dt>
                <dd>{formatDateTime(store.lastSyncAt)}</dd>
                <dt className="text-muted-foreground">Last activity</dt>
                <dd>{formatDateTime(store.lastActivityAt)}</dd>
              </dl>
              <div className="mt-3">
                <ManageLink store={store} />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ManageLink({ store }: { store: Store }) {
  return (
    <Button asChild variant="outline" size="sm" className="min-h-11 md:min-h-9">
      <Link href="/settings/integrations" aria-label={`Manage ${store.name} under Integrations`}>
        Manage
      </Link>
    </Button>
  );
}
