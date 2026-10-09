"use client";

import { useState } from "react";

import {
  PlatformPageHeader,
  RequirePermission,
  usePlatformAccess,
} from "@/components/platform/platform-shell";
import { ReasonedAction } from "@/components/platform/platform-workspace-actions";
import { Facts } from "@/components/platform/platform-workspaces";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/utils";
import {
  type FlagState,
  useGlobalFlags,
  useSetGlobalFlag,
  useWorkspaceBilling,
  useWorkspaceBillingChange,
} from "@/services/platform";

/**
 * Admin Control Center phase 8 (D-019): a workspace's subscription, trial,
 * plan override and feature switches. Billing changes need re-authentication
 * and a reason; they are account-level, so no support session.
 */
export function BillingTab({ tenantId }: { tenantId: string }) {
  const { can } = usePlatformAccess();
  const billing = useWorkspaceBilling(tenantId);
  const change = useWorkspaceBillingChange(tenantId);
  const [days, setDays] = useState(14);
  const [plan, setPlan] = useState<"starter" | "growth" | "pro">("growth");
  const [ai, setAi] = useState(false);
  const [overrideDays, setOverrideDays] = useState(30);

  if (!can("billing.read")) {
    return (
      <Alert>
        <AlertDescription>Your role cannot see billing.</AlertDescription>
      </Alert>
    );
  }
  if (billing.isError) {
    return (
      <ErrorState
        title="Could not load billing"
        onRetry={() => void billing.refetch()}
      />
    );
  }
  const b = billing.data;
  if (!b) return <Skeleton className="h-64 w-full" />;
  const manage = can("billing.manage");

  return (
    <div
      className="grid gap-4 lg:grid-cols-2"
      data-testid="platform-workspace-billing"
    >
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Subscription</CardTitle>
          {!b.billingEnforced && (
            <CardDescription>
              Stripe is not configured on this deployment, so limits are not
              enforced.
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <Facts
            rows={[
              ["Plan", b.plan ?? (b.onTrial ? "trial" : "none")],
              ["Stripe status", b.status],
              ["Trial ends", formatDateTime(b.trialEndsAt)],
              [
                "Period ends",
                b.currentPeriodEnd ? formatDateTime(b.currentPeriodEnd) : "—",
              ],
              ["Listings", `${b.listingsUsed} of ${b.listingLimit}`],
              ["Can publish", b.canWrite ? "yes" : "no"],
              ["AI", b.canUseAi ? "yes" : "no"],
              [
                "Override",
                b.planOverride
                  ? `${b.planOverride}${b.planOverrideAi ? " + AI" : ""} until ${formatDateTime(b.planOverrideUntil)}`
                  : "none",
              ],
            ]}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Feature switches</CardTitle>
          <CardDescription>
            This workspace&apos;s override, or the platform default.
          </CardDescription>
        </CardHeader>
        <CardContent
          className="space-y-3"
          data-testid="platform-workspace-flags"
        >
          {b.flags.map((flag) => (
            <FlagRow
              key={flag.key}
              flag={flag}
              manage={manage}
              tenantId={tenantId}
            />
          ))}
        </CardContent>
      </Card>

      {manage && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Extend the trial</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Label htmlFor="trial-days">Days to add</Label>
              <select
                id="trial-days"
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
              >
                {[7, 14, 30, 60, 90].map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              <ReasonedAction
                label="Extend trial"
                description="Counted from today or the current end, whichever is later. The workspace is notified."
                onRun={(reason) =>
                  change.mutateAsync({
                    reason,
                    change: { kind: "trial", days },
                  })
                }
              />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Plan override</CardTitle>
              <CardDescription>
                Grants a plan for a limited time, whatever Stripe says. Always
                ends on its own.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="grid grid-cols-3 gap-2">
                <div className="space-y-1">
                  <Label htmlFor="override-plan">Plan</Label>
                  <select
                    id="override-plan"
                    className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                    value={plan}
                    onChange={(e) =>
                      setPlan(e.target.value as "starter" | "growth" | "pro")
                    }
                  >
                    <option value="starter">starter</option>
                    <option value="growth">growth</option>
                    <option value="pro">pro</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="override-days">Days</Label>
                  <select
                    id="override-days"
                    className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                    value={overrideDays}
                    onChange={(e) => setOverrideDays(Number(e.target.value))}
                  >
                    {[7, 30, 90, 180, 365].map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </div>
                <label className="flex items-end gap-2 pb-2 text-sm">
                  <input
                    type="checkbox"
                    checked={ai}
                    onChange={(e) => setAi(e.target.checked)}
                  />
                  AI add-on
                </label>
              </div>
              <ReasonedAction
                label="Apply override"
                onRun={(reason) =>
                  change.mutateAsync({
                    reason,
                    change: {
                      kind: "plan-override",
                      plan,
                      ai,
                      days: overrideDays,
                    },
                  })
                }
              />
              {b.planOverride && (
                <ReasonedAction
                  label="Remove override"
                  destructive
                  onRun={(reason) =>
                    change.mutateAsync({
                      reason,
                      change: { kind: "clear-override" },
                    })
                  }
                />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function FlagRow({
  flag,
  manage,
  tenantId,
}: {
  flag: FlagState;
  manage: boolean;
  tenantId: string;
}) {
  const change = useWorkspaceBillingChange(tenantId);
  const next = flag.override === null ? !flag.effective : null;
  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-sm font-medium">{flag.key.replaceAll("_", " ")}</p>
          <p className="text-xs text-muted-foreground">{flag.description}</p>
        </div>
        <Badge variant={flag.effective ? "outline" : "destructive"}>
          {flag.effective ? "on" : "off"}
          {flag.override !== null ? " (override)" : ""}
        </Badge>
      </div>
      {manage && (
        <ReasonedAction
          label={
            next === null
              ? "Remove override (use platform default)"
              : next
                ? "Switch on for this workspace"
                : "Switch off for this workspace"
          }
          destructive={next === false}
          id={`flag-reason-${flag.key}`}
          onRun={(reason) =>
            change.mutateAsync({
              reason,
              change: { kind: "flag", key: flag.key, enabled: next },
            })
          }
        />
      )}
    </div>
  );
}

/** Platform-wide defaults of the feature switches (super admin changes). */
export function PlatformFeatureFlags() {
  return (
    <RequirePermission permission="billing.read">
      <PlatformPageHeader
        title="Feature switches"
        description="Platform-wide defaults. A workspace override, set on its Billing tab, wins."
      />
      <FlagDefaults />
    </RequirePermission>
  );
}

function FlagDefaults() {
  const { can } = usePlatformAccess();
  const flags = useGlobalFlags();
  const set = useSetGlobalFlag();
  if (flags.isError) {
    return (
      <ErrorState
        title="Could not load switches"
        onRetry={() => void flags.refetch()}
      />
    );
  }
  if (!flags.data) return <Skeleton className="h-48 w-full" />;
  return (
    <div
      className="grid gap-3 md:grid-cols-2"
      data-testid="platform-global-flags"
    >
      {flags.data.map((flag) => (
        <Card key={flag.key}>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center justify-between text-base">
              {flag.key.replaceAll("_", " ")}
              <Badge variant={flag.enabled ? "outline" : "destructive"}>
                {flag.enabled ? "on" : "off"}
              </Badge>
            </CardTitle>
            <CardDescription>{flag.description}</CardDescription>
          </CardHeader>
          <CardContent>
            {can("settings.manage") ? (
              <ReasonedAction
                label={
                  flag.enabled
                    ? "Switch off everywhere"
                    : "Switch on everywhere"
                }
                destructive={flag.enabled}
                onRun={(reason) =>
                  set.mutateAsync({
                    key: flag.key,
                    enabled: !flag.enabled,
                    reason,
                  })
                }
              />
            ) : (
              <p className="text-xs text-muted-foreground">
                Only a super admin changes defaults.
              </p>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
