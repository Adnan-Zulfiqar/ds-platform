"use client";

import { History, Pencil, Power, PowerOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SCOPE_LABEL, StatusBadge } from "@/components/global-rules/rule-primitives";
import { formatDateTime } from "@/lib/utils";
import { SCOPE_IDENTIFIER, type RuleScope } from "@/services/global-rules";

/**
 * The rule list.
 *
 * Rendered as cards at every width rather than a table that collapses.
 * A rule row carries eight independent facts — status, name, scope, the
 * identifier it targets, priority, version, when it changed, and its actions —
 * and a table of eight columns is unreadable on a phone whichever way it is
 * folded. Cards read the same everywhere and never overflow horizontally.
 */

export interface RuleListRow {
  id: string;
  name: string;
  scope: RuleScope;
  storeId: string | null;
  categoryId: string | null;
  productId: string | null;
  variantId: string | null;
  priority: number;
  version: number;
  isActive: boolean;
  updatedAt: string;
  /** Kind-specific summary line, e.g. "50% markup" or "Cheapest tracked to GB". */
  summary: string;
}

function scopeTarget(row: RuleListRow, storeNames: Record<string, string>): string | null {
  const key = SCOPE_IDENTIFIER[row.scope];
  if (!key) return null;
  const value = row[key];
  if (!value) return null;
  if (key === "storeId") return storeNames[value] ?? value;
  return value;
}

export function RuleList({
  rows,
  storeNames,
  canManage,
  busyRuleId,
  onEdit,
  onToggleActive,
  onHistory,
}: {
  rows: RuleListRow[];
  storeNames: Record<string, string>;
  canManage: boolean;
  busyRuleId: string | null;
  onEdit: (id: string) => void;
  onToggleActive: (row: RuleListRow) => void;
  onHistory: (id: string) => void;
}) {
  return (
    <ul className="space-y-3" data-testid="rule-list">
      {rows.map((row) => {
        const target = scopeTarget(row, storeNames);
        const busy = busyRuleId === row.id;
        return (
          <li
            key={row.id}
            data-testid="rule-row"
            data-rule-id={row.id}
            data-active={row.isActive}
            className="rounded-lg border p-4"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge active={row.isActive} />
                  <span className="font-medium">{row.name}</span>
                  <Badge variant="outline">{SCOPE_LABEL[row.scope]}</Badge>
                </div>
                <p className="text-sm text-muted-foreground">{row.summary}</p>
                {target && (
                  <p className="break-all font-mono text-xs text-muted-foreground">
                    {target}
                  </p>
                )}
                <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <div className="flex gap-1">
                    <dt>Priority</dt>
                    <dd className="font-medium text-foreground">{row.priority}</dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>Version</dt>
                    <dd
                      className="font-medium text-foreground"
                      data-testid="rule-version"
                    >
                      {row.version}
                    </dd>
                  </div>
                  <div className="flex gap-1">
                    <dt>Updated</dt>
                    <dd className="font-medium text-foreground">
                      {formatDateTime(row.updatedAt)}
                    </dd>
                  </div>
                </dl>
              </div>

              <div className="flex flex-wrap gap-2 sm:shrink-0">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => onHistory(row.id)}
                  className="min-h-10"
                >
                  <History className="mr-2 h-4 w-4" aria-hidden="true" />
                  History
                </Button>
                {/* Mutation controls are absent for a viewer, not disabled: a
                    disabled control that keyboard focus lands on and does
                    nothing is worse than one that was never there. The backend
                    rejects the call regardless — this is presentation. */}
                {canManage && (
                  <>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => onEdit(row.id)}
                      className="min-h-10"
                    >
                      <Pencil className="mr-2 h-4 w-4" aria-hidden="true" />
                      Edit
                    </Button>
                    <Button
                      type="button"
                      variant={row.isActive ? "outline" : "default"}
                      size="sm"
                      disabled={busy}
                      onClick={() => onToggleActive(row)}
                      className="min-h-10"
                    >
                      {row.isActive ? (
                        <PowerOff className="mr-2 h-4 w-4" aria-hidden="true" />
                      ) : (
                        <Power className="mr-2 h-4 w-4" aria-hidden="true" />
                      )}
                      {row.isActive ? "Deactivate" : "Activate"}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
