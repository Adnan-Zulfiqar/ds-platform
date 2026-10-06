"use client";

import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/providers/auth-provider";
import {
  type FulfilmentSettings,
  useFulfilmentSettings,
  useSaveFulfilmentSettings,
} from "@/services/orders";

/**
 * Settings → Fulfilment (Track F, D-017). Both switches start off. The
 * draft is local until Save, so React Query keeps the saved values.
 */
export function FulfilmentSettingsForm() {
  const { hasRole } = useAuth();
  const canEdit = hasRole("owner") || hasRole("admin");
  const { data, isLoading, isError, refetch } = useFulfilmentSettings();
  const save = useSaveFulfilmentSettings();
  const [draft, setDraft] = useState<FulfilmentSettings | null>(null);

  if (isError) {
    return <ErrorState title="Could not load fulfilment settings" onRetry={() => void refetch()} />;
  }
  if (isLoading || !data) return <Skeleton className="h-64 w-full" />;

  const current = draft ?? data;
  const dirty =
    current.autoOrder !== data.autoOrder ||
    current.autoTracking !== data.autoTracking ||
    (current.fallbackShippingMethod ?? "") !== (data.fallbackShippingMethod ?? "");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">AliExpress orders</CardTitle>
        <CardDescription>
          Orders are created unpaid on AliExpress. You still pay them there, so turning on
          automatic ordering never spends money by itself.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <label className="flex items-start gap-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4"
            checked={current.autoOrder}
            disabled={!canEdit}
            onChange={(event) => setDraft({ ...current, autoOrder: event.target.checked })}
          />
          <span>
            <span className="font-medium">Place paid orders on AliExpress automatically</span>
            <span className="block text-muted-foreground">
              Only orders whose every item maps to one AliExpress variant. Anything unclear waits
              for you on the order page.
            </span>
          </span>
        </label>
        <label className="flex items-start gap-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4"
            checked={current.autoTracking}
            disabled={!canEdit}
            onChange={(event) => setDraft({ ...current, autoTracking: event.target.checked })}
          />
          <span>
            <span className="font-medium">Send AliExpress tracking to the store automatically</span>
            <span className="block text-muted-foreground">
              Checked every three hours. Your customer gets the store&apos;s usual shipping email.
            </span>
          </span>
        </label>
        <div className="space-y-1">
          <Label htmlFor="fallback-shipping">Fallback shipping method (optional)</Label>
          <Input
            id="fallback-shipping"
            placeholder="Leave empty to use the supplier's default"
            value={current.fallbackShippingMethod ?? ""}
            disabled={!canEdit}
            onChange={(event) =>
              setDraft({ ...current, fallbackShippingMethod: event.target.value })
            }
          />
        </div>
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
        {canEdit ? (
          <Button
            disabled={!dirty || save.isPending}
            onClick={() =>
              save.mutate(
                {
                  ...current,
                  fallbackShippingMethod: (current.fallbackShippingMethod ?? "").trim() || null,
                },
                { onSuccess: () => setDraft(null) },
              )
            }
          >
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        ) : (
          <p className="text-sm text-muted-foreground">Only owners and admins can change these.</p>
        )}
      </CardContent>
    </Card>
  );
}
