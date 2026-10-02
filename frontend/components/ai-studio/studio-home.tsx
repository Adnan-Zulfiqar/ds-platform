"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState, useSyncExternalStore } from "react";

import { BulkStartDialog } from "@/components/ai-studio/bulk-start-dialog";
import { RunDashboard } from "@/components/ai-studio/run-dashboard";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { MAX_BULK_SELECTION, readActiveRun, writeActiveRun } from "@/lib/ai-studio/bulk";
import { requestIdOf, studioErrorMessage } from "@/lib/ai-studio/errors";
import { useAuth } from "@/providers/auth-provider";
import { useDrafts } from "@/services/drafts";
import { useProducts } from "@/services/products";
import { useStores } from "@/services/stores";
import type { PipelineBulkRun, Product } from "@/types/api";

type Lifecycle = "drafts" | "published";

function subscribeToStorage(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}
const PAGE_SIZE = 20;

/**
 * `/ai-studio` — select up to 50 drafts and published products, start one
 * bulk preview run, and follow it (plan §18–§24).
 *
 * Selection is one set of product ids for the visit; it survives paging and
 * switching between Drafts and Published, and can never exceed the Stage 9
 * cap, so the start request is exactly what the merchant selected.
 */
export function StudioHome() {
  const router = useRouter();
  const runId = useSearchParams().get("run");
  const { identity } = useAuth();
  const tenantId = identity?.tenant.id ?? null;

  const [lifecycle, setLifecycle] = useState<Lifecycle>("drafts");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [dialogOpen, setDialogOpen] = useState(false);

  const query = { page, size: PAGE_SIZE, ...(search ? { q: search } : {}) };
  const drafts = useDrafts(query);
  const published = useProducts(query);
  const list = lifecycle === "drafts" ? drafts : published;
  const stores = useStores({ size: 50 });
  const shopifyStores = useMemo(
    () => stores.data?.items.filter((store) => store.platform === "shopify") ?? [],
    [stores.data],
  );

  // sessionStorage is an external store with no server value: the server
  // snapshot (null) is also the hydration value, so the two HTML trees agree.
  // Another tab writing the key fires "storage"; this tab re-reads on render.
  const storedRun = useSyncExternalStore(
    subscribeToStorage,
    () => (tenantId ? readActiveRun(tenantId) : null),
    () => null,
  );
  const atCap = selected.size >= MAX_BULK_SELECTION;
  // "Select page" acts on the rows of the page being shown. While a view or
  // page loads, the list is empty or still shows the previous page
  // (placeholder data), so selecting then would silently add nothing or the
  // wrong page.
  const pageReady = list.data !== undefined && !list.isPlaceholderData;

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else if (next.size < MAX_BULK_SELECTION) next.add(id);
      return next;
    });
  }

  function selectPage() {
    setSelected((current) => {
      const next = new Set(current);
      for (const product of list.data?.items ?? []) {
        if (next.size >= MAX_BULK_SELECTION) break;
        next.add(product.id);
      }
      return next;
    });
  }

  function switchLifecycle(next: Lifecycle) {
    setLifecycle(next);
    setPage(1);
  }

  function started(run: PipelineBulkRun) {
    if (tenantId) writeActiveRun(tenantId, run.id);
    setDialogOpen(false);
    setSelected(new Set());
    router.push(`/ai-studio?run=${run.id}`);
  }

  if (runId) {
    return (
      <div className="space-y-4">
        <Button asChild variant="outline" size="sm">
          <Link href="/ai-studio">New selection</Link>
        </Button>
        <RunDashboard runId={runId} />
      </div>
    );
  }

  const viewLabel = lifecycle === "drafts" ? "Drafts" : "Published";

  return (
    <div className="space-y-4">
      {storedRun ? (
        <p className="text-sm" data-testid="ai-studio-stored-run">
          A bulk run you started is still open.{" "}
          <Link className="underline" href={`/ai-studio?run=${storedRun}`}>
            Open it
          </Link>
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant={lifecycle === "drafts" ? "default" : "outline"}
          aria-pressed={lifecycle === "drafts"}
          onClick={() => switchLifecycle("drafts")}
          data-testid="ai-studio-view-drafts"
        >
          Drafts
        </Button>
        <Button
          type="button"
          variant={lifecycle === "published" ? "default" : "outline"}
          aria-pressed={lifecycle === "published"}
          onClick={() => switchLifecycle("published")}
          data-testid="ai-studio-view-published"
        >
          Published
        </Button>
        <Input
          type="search"
          aria-label="Search products"
          placeholder="Search products"
          className="w-full sm:w-64"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
        />
      </div>

      <div
        className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-background p-3"
        data-testid="ai-studio-selection-bar"
      >
        <p className="text-sm" data-testid="ai-studio-selection-count">
          {selected.size} / {MAX_BULK_SELECTION} selected
        </p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" onClick={selectPage} disabled={atCap || !pageReady}>
            Select page
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setSelected(new Set())}
            disabled={selected.size === 0}
          >
            Clear
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => setDialogOpen(true)}
            disabled={selected.size === 0}
            data-testid="ai-studio-bulk-start"
          >
            Generate previews
          </Button>
        </div>
        {atCap ? (
          <p className="w-full text-xs text-muted-foreground" data-testid="ai-studio-cap-note">
            You can optimize up to 50 products at a time.
          </p>
        ) : null}
      </div>

      <section aria-label={viewLabel} data-testid="ai-studio-list" data-view={lifecycle}>
        <h2 className="sr-only">{viewLabel}</h2>
        {list.isPending ? (
          <Skeleton className="h-64 w-full" />
        ) : list.isError ? (
          <ErrorState
            title={`Could not load ${viewLabel.toLowerCase()}`}
            description={studioErrorMessage(list.error)}
            requestId={requestIdOf(list.error)}
            onRetry={() => void list.refetch()}
          />
        ) : list.data.items.length === 0 ? (
          <EmptyState
            title={lifecycle === "drafts" ? "No drafts" : "Nothing published yet"}
            description={
              lifecycle === "drafts"
                ? "Import a product to create a draft you can optimize here."
                : "Products appear here once they are published to a store."
            }
            action={
              lifecycle === "drafts" ? (
                <Button asChild size="sm">
                  <Link href="/drafts">Go to Drafts</Link>
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ProductRows
            products={list.data.items}
            selected={selected}
            atCap={atCap}
            onToggle={toggle}
          />
        )}
        {list.data && list.data.meta.totalPages > 1 ? (
          <div className="mt-3 flex items-center justify-end gap-2 text-sm">
            <Button type="button" size="sm" variant="outline" disabled={!list.data.meta.hasPrevious} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <span>
              Page {list.data.meta.page} of {list.data.meta.totalPages}
            </span>
            <Button type="button" size="sm" variant="outline" disabled={!list.data.meta.hasNext} onClick={() => setPage(page + 1)}>
              Next
            </Button>
          </div>
        ) : null}
      </section>

      <BulkStartDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        productIds={[...selected]}
        stores={shopifyStores}
        tenantId={tenantId}
        onStarted={started}
      />
    </div>
  );
}

function ProductRows({
  products,
  selected,
  atCap,
  onToggle,
}: {
  products: Product[];
  selected: ReadonlySet<string>;
  atCap: boolean;
  onToggle: (id: string) => void;
}) {
  return (
    <ul className="divide-y rounded-lg border">
      {products.map((product) => {
        const checked = selected.has(product.id);
        const inputId = `ai-studio-select-${product.id}`;
        return (
          <li
            key={product.id}
            className="flex items-center gap-3 p-3 text-sm"
            data-testid="ai-studio-product-row"
          >
            <input
              id={inputId}
              type="checkbox"
              className="h-4 w-4"
              checked={checked}
              disabled={!checked && atCap}
              onChange={() => onToggle(product.id)}
            />
            <label htmlFor={inputId} className="min-w-0 flex-1 truncate">
              {product.title || "Untitled product"}
            </label>
            <Button asChild size="sm" variant="outline">
              <Link href={`/ai-studio/products/${product.id}`}>Review</Link>
            </Button>
          </li>
        );
      })}
    </ul>
  );
}
