"use client";

import { useState, type ReactNode } from "react";

import { Pager } from "@/components/platform/platform-workspaces";
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
import {
  type WorkspaceListParams,
  type WorkspaceResource,
  usePlatformWorkspaceList,
} from "@/services/platform";

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
}

export interface Filter {
  /** Query parameter name, as the API expects it. */
  param: string;
  label: string;
  options: { value: string; label: string }[];
}

/**
 * One paged, searchable, filterable list inside a workspace (phase 3). Every
 * page it loads is a server-checked, audited view; nothing is cached across
 * workspaces because the workspace id is part of every query key.
 */
export function PlatformDataTable<T extends { id: string }>({
  tenantId,
  resource,
  columns,
  filters = [],
  fixed = {},
  searchable = true,
  onRowClick,
  emptyText,
  testId,
}: {
  tenantId: string;
  resource: WorkspaceResource;
  columns: Column<T>[];
  filters?: Filter[];
  /** Parameters this view always sends (e.g. `publication=draft`). */
  fixed?: WorkspaceListParams;
  searchable?: boolean;
  onRowClick?: (row: T) => void;
  emptyText: string;
  testId?: string;
}) {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const list = usePlatformWorkspaceList<T>(tenantId, resource, {
    ...fixed,
    ...chosen,
    q: q || undefined,
    page,
    size: 25,
  });

  return (
    <div className="space-y-3">
      {(searchable || filters.length > 0) && (
        <form
          className="flex flex-wrap items-end gap-2"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setPage(1);
            setQ(query.trim());
          }}
        >
          {searchable && (
            <Input
              className="max-w-xs"
              aria-label={`Search ${resource}`}
              placeholder="Search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          )}
          {filters.map((f) => (
            <label
              key={f.param}
              className="flex flex-col gap-1 text-xs text-muted-foreground"
            >
              {f.label}
              <select
                className="h-9 rounded-md border bg-background px-2 text-sm text-foreground"
                value={chosen[f.param] ?? ""}
                onChange={(e) => {
                  setPage(1);
                  setChosen({ ...chosen, [f.param]: e.target.value });
                }}
              >
                <option value="">All</option>
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {searchable && (
            <Button type="submit" variant="outline">
              Search
            </Button>
          )}
        </form>
      )}
      {list.isError ? (
        <ErrorState
          title="Could not load this list"
          onRetry={() => void list.refetch()}
        />
      ) : !list.data ? (
        <Skeleton className="h-48 w-full" />
      ) : list.data.items.length === 0 ? (
        <EmptyState title="Nothing here" description={emptyText} />
      ) : (
        <>
          <div
            className="overflow-x-auto rounded-lg border"
            data-testid={testId}
          >
            <Table>
              <TableHeader>
                <TableRow>
                  {columns.map((c) => (
                    <TableHead key={c.header} className={c.className}>
                      {c.header}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((row) => (
                  <TableRow
                    key={row.id}
                    className={onRowClick ? "cursor-pointer" : undefined}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    tabIndex={onRowClick ? 0 : undefined}
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.key === "Enter") onRowClick(row);
                          }
                        : undefined
                    }
                  >
                    {columns.map((c) => (
                      <TableCell key={c.header} className={c.className}>
                        {c.cell(row)}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="flex items-center justify-between gap-2">
            <Pager
              page={page}
              totalPages={list.data.meta.totalPages}
              hasNext={list.data.meta.hasNext}
              onPage={setPage}
            />
            <span className="text-xs text-muted-foreground">
              {list.data.meta.totalItems} in total
            </span>
          </div>
        </>
      )}
    </div>
  );
}
