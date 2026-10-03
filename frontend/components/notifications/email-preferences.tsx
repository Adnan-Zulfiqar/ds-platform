"use client";

import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type NotificationKind,
  useEmailPreferences,
  useSaveEmailPreferences,
} from "@/services/notifications";

const LABELS: Record<NotificationKind, string> = {
  import_completed: "Product import finished",
  sync_failed: "Sync failed",
  inventory_changed: "Inventory changed",
  price_changed: "Price changed",
  order_imported: "Order imported",
  shipment_updated: "Shipment updated",
  webhook_failure: "Webhook failure",
  task_failure: "Background task failed",
  automation_completed: "Automation finished",
  automation_failed: "Automation failed",
  info: "Information",
};

/**
 * Track E3: the signed-in user's own email choices. Failures are on by
 * default; routine progress stays in the app unless the user opts in.
 *
 * Edits are a local draft over the server value until Save, so a misclick
 * sends nothing. The draft is `null` while untouched rather than a copy of
 * the response, which keeps React Query the only owner of the saved state.
 */
export function EmailPreferencesForm() {
  const { data, isLoading, isError, refetch } = useEmailPreferences();
  const save = useSaveEmailPreferences();
  const [draft, setDraft] = useState<Set<NotificationKind> | null>(null);

  if (isError) {
    return <ErrorState title="Could not load email preferences" onRetry={() => void refetch()} />;
  }
  if (isLoading || !data) return <Skeleton className="h-64 w-full" />;

  const selected = draft ?? new Set(data.kinds);
  const dirty = selected.size !== data.kinds.length || data.kinds.some((k) => !selected.has(k));

  function toggle(kind: NotificationKind) {
    const next = new Set(selected);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    setDraft(next);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Email me about</CardTitle>
        <CardDescription>
          Notifications for the whole workspace go to owners and admins. Ones meant for you alone
          go only to you.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="divide-y rounded-lg border" data-testid="email-preferences">
          {data.available.map((kind) => {
            const id = `email-pref-${kind}`;
            return (
              <li key={kind} className="flex items-center gap-3 p-3 text-sm">
                <input
                  id={id}
                  type="checkbox"
                  className="h-4 w-4"
                  checked={selected.has(kind)}
                  onChange={() => toggle(kind)}
                />
                <label htmlFor={id} className="flex-1">
                  {LABELS[kind]}
                </label>
              </li>
            );
          })}
        </ul>
        {save.isError && (
          <Alert variant="destructive">
            <AlertDescription>Could not save. Please try again.</AlertDescription>
          </Alert>
        )}
        {save.isSuccess && !dirty && (
          <p className="text-sm text-muted-foreground" role="status">
            Saved.
          </p>
        )}
        <Button
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate([...selected].sort(), { onSuccess: () => setDraft(null) })}
        >
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </CardContent>
    </Card>
  );
}
