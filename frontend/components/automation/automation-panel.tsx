"use client";

import { Bot } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDeleteButton } from "@/components/ui/confirm-delete-button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import {
  useAutomationRules,
  useCreateAutomationRule,
  useDeleteAutomationRule,
  useRunAutomation,
  useUpdateAutomationRule,
  type AutomationAction,
  type AutomationRule,
  type AutomationSchedule,
} from "@/services/automation";

const ACTIONS: Array<{ value: AutomationAction; label: string }> = [
  { value: "sync_inventory", label: "Sync inventory" },
  { value: "update_pricing", label: "Update pricing" },
  { value: "refresh_orders", label: "Refresh orders" },
  { value: "archive_completed_orders", label: "Archive completed orders" },
  { value: "retry_failed_jobs", label: "Retry failed jobs" },
  { value: "import_product", label: "Import product" },
];

const SCHEDULES: AutomationSchedule[] = ["manual", "hourly", "daily", "weekly"];

const SELECT_CLASS =
  "h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm";

function describe(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/**
 * Rules are edited in place, one row at a time. The draft lives in local
 * state until Save, so the React Query row stays the only saved truth, and
 * only the fields the API accepts for an update (name, schedule, active)
 * are editable; the action is fixed at creation.
 */
export function AutomationPanel() {
  const { data, isLoading, isError, refetch } = useAutomationRules({
    page: 1,
    size: 50,
  });
  const create = useCreateAutomationRule();
  const update = useUpdateAutomationRule();
  const remove = useDeleteAutomationRule();
  const run = useRunAutomation();
  const [name, setName] = useState("Nightly inventory sync");
  const [action, setAction] = useState<AutomationAction>("sync_inventory");
  const [schedule, setSchedule] = useState<AutomationSchedule>("daily");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [draft, setDraft] = useState<{
    id: string;
    name: string;
    schedule: AutomationSchedule;
  } | null>(null);

  if (isError) {
    return (
      <ErrorState
        title="Could not load automation rules"
        onRetry={() => void refetch()}
      />
    );
  }

  const rules = data?.items ?? [];
  const busy = update.isPending || remove.isPending;

  function saveDraft() {
    if (!draft) return;
    update.mutate(
      { id: draft.id, name: draft.name, schedule: draft.schedule },
      {
        onSuccess: () => {
          setDraft(null);
          setStatus("Rule updated.");
        },
        onError: (err) => setStatus(describe(err, "Update failed.")),
      },
    );
  }

  function toggleActive(rule: AutomationRule) {
    update.mutate(
      { id: rule.id, isActive: !rule.isActive },
      { onError: (err) => setStatus(describe(err, "Update failed.")) },
    );
  }

  function deleteRule(rule: AutomationRule) {
    remove.mutate(rule.id, {
      onSuccess: () => setStatus(`Deleted "${rule.name}".`),
      onError: (err) => setStatus(describe(err, "Delete failed.")),
    });
  }

  return (
    <div className="space-y-6">
      <form
        className="grid gap-4 rounded-md border p-4 sm:grid-cols-2 lg:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          setError(null);
          create.mutate(
            { name, action, schedule },
            {
              onError: (err) => setError(describe(err, "Create failed.")),
            },
          );
        }}
      >
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="auto-name">Name</Label>
          <Input
            id="auto-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="auto-action">Action</Label>
          <select
            id="auto-action"
            aria-label="Action"
            value={action}
            onChange={(event) => setAction(event.target.value as AutomationAction)}
            className={SELECT_CLASS}
          >
            {ACTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="auto-schedule">Schedule</Label>
          <select
            id="auto-schedule"
            aria-label="Schedule"
            value={schedule}
            onChange={(event) =>
              setSchedule(event.target.value as AutomationSchedule)
            }
            className={SELECT_CLASS}
          >
            {SCHEDULES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-end sm:col-span-2 lg:col-span-4">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create rule"}
          </Button>
          {error && <p className="ml-3 text-sm text-destructive">{error}</p>}
          {status && (
            <p className="ml-3 text-sm text-muted-foreground" role="status">
              {status}
            </p>
          )}
        </div>
      </form>

      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : rules.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="No automation rules"
          description="Create a rule to sync inventory, update pricing, or refresh orders on a schedule."
        />
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Schedule</TableHead>
                <TableHead>Last run</TableHead>
                <TableHead>Failures</TableHead>
                <TableHead>Active</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.map((rule) => {
                const editing = draft?.id === rule.id;
                return (
                  <TableRow key={rule.id} data-testid="automation-rule-row">
                    <TableCell className="font-medium">
                      {editing ? (
                        <Input
                          aria-label={`Name of ${rule.name}`}
                          value={draft.name}
                          onChange={(event) =>
                            setDraft({ ...draft, name: event.target.value })
                          }
                        />
                      ) : (
                        rule.name
                      )}
                    </TableCell>
                    <TableCell>{rule.action}</TableCell>
                    <TableCell>
                      {editing ? (
                        <select
                          aria-label={`Schedule of ${rule.name}`}
                          value={draft.schedule}
                          onChange={(event) =>
                            setDraft({
                              ...draft,
                              schedule: event.target.value as AutomationSchedule,
                            })
                          }
                          className={SELECT_CLASS}
                        >
                          {SCHEDULES.map((value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ))}
                        </select>
                      ) : (
                        rule.schedule
                      )}
                    </TableCell>
                    <TableCell>{formatDateTime(rule.lastRunAt)}</TableCell>
                    <TableCell>{rule.consecutiveFailures}</TableCell>
                    <TableCell>
                      <Badge variant={rule.isActive ? "success" : "secondary"}>
                        {rule.isActive ? "active" : "inactive"}
                      </Badge>
                    </TableCell>
                    <TableCell className="space-x-1 whitespace-nowrap text-right">
                      {editing ? (
                        <>
                          <Button
                            size="sm"
                            disabled={busy || draft.name.trim() === ""}
                            onClick={saveDraft}
                          >
                            Save
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => setDraft(null)}
                          >
                            Cancel
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={run.isPending}
                            onClick={() => {
                              run.mutate(rule.id, {
                                onSuccess: (result) =>
                                  setStatus(`Run ${result.id}: ${result.status}`),
                                onError: (err) =>
                                  setStatus(describe(err, "Run failed.")),
                              });
                            }}
                          >
                            Run now
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            aria-label={`Edit ${rule.name}`}
                            onClick={() =>
                              setDraft({
                                id: rule.id,
                                name: rule.name,
                                schedule: rule.schedule,
                              })
                            }
                          >
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            aria-label={`${rule.isActive ? "Pause" : "Resume"} ${rule.name}`}
                            onClick={() => toggleActive(rule)}
                          >
                            {rule.isActive ? "Pause" : "Resume"}
                          </Button>
                          <ConfirmDeleteButton
                            label={rule.name}
                            disabled={busy}
                            onConfirm={() => deleteRule(rule)}
                          />
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
