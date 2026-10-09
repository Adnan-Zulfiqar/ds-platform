"use client";

import { useState } from "react";

import {
  PlatformPageHeader,
  RequirePermission,
  usePlatformAccess,
} from "@/components/platform/platform-shell";
import { ReasonedAction } from "@/components/platform/platform-workspace-actions";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { formatDateTime } from "@/lib/utils";
import {
  usePlatformBroadcast,
  usePlatformSettings,
  usePlatformSettingsChange,
} from "@/services/platform";

/**
 * Admin Control Center phase 10 (D-019): maintenance mode, announcements and
 * broadcasts. Everyone with dashboard access sees the current state; only a
 * super admin changes it, after re-authenticating, with a reason.
 */
export function PlatformSettings() {
  return (
    <RequirePermission permission="dashboard.read">
      <PlatformPageHeader
        title="Platform settings"
        description="What every merchant sees and can do, platform-wide."
      />
      <Settings />
    </RequirePermission>
  );
}

function Settings() {
  const { can } = usePlatformAccess();
  const manage = can("settings.manage");
  const settings = usePlatformSettings();
  const change = usePlatformSettingsChange();
  const broadcast = usePlatformBroadcast();
  const [message, setMessage] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [level, setLevel] = useState<"info" | "warning" | "critical">("info");
  const [endsAt, setEndsAt] = useState("");
  const [bTitle, setBTitle] = useState("");
  const [bBody, setBBody] = useState("");

  if (settings.isError) {
    return (
      <ErrorState
        title="Could not load settings"
        onRetry={() => void settings.refetch()}
      />
    );
  }
  const s = settings.data;
  if (!s) return <Skeleton className="h-64 w-full" />;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card data-testid="platform-maintenance">
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-base">
            Maintenance mode
            <Badge variant={s.maintenance.enabled ? "destructive" : "outline"}>
              {s.maintenance.enabled ? "on" : "off"}
            </Badge>
          </CardTitle>
          <CardDescription>
            While on, merchants can sign in and view everything but cannot
            change anything. Webhooks, OAuth returns and background jobs keep
            running.
            {s.maintenance.updatedAt
              ? ` Last changed ${formatDateTime(s.maintenance.updatedAt)}.`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {s.maintenance.message && (
            <p className="text-sm">Message: {s.maintenance.message}</p>
          )}
          {manage ? (
            <>
              {!s.maintenance.enabled && (
                <div className="space-y-1">
                  <Label htmlFor="maintenance-message">
                    Message for merchants (optional)
                  </Label>
                  <Input
                    id="maintenance-message"
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                  />
                </div>
              )}
              <ReasonedAction
                label={
                  s.maintenance.enabled
                    ? "End maintenance"
                    : "Start maintenance"
                }
                destructive={!s.maintenance.enabled}
                onRun={(reason) =>
                  change.mutateAsync({
                    reason,
                    change: {
                      kind: "maintenance",
                      enabled: !s.maintenance.enabled,
                      message: s.maintenance.enabled
                        ? null
                        : message.trim() || null,
                    },
                  })
                }
              />
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              Only a super admin changes this.
            </p>
          )}
        </CardContent>
      </Card>

      <Card data-testid="platform-announcements">
        <CardHeader>
          <CardTitle className="text-base">Announcements</CardTitle>
          <CardDescription>
            A banner across the top of the merchant app while live.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {s.announcements.length === 0 ? (
            <p className="text-sm text-muted-foreground">None yet.</p>
          ) : (
            <ul className="divide-y rounded border text-sm">
              {s.announcements.map((a) => {
                const live =
                  new Date(a.startsAt) <= new Date() &&
                  (a.endsAt === null || new Date(a.endsAt) > new Date());
                return (
                  <li key={a.id} className="space-y-1 p-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{a.title}</span>
                      <span className="flex gap-1">
                        <Badge variant="outline">{a.level}</Badge>
                        <Badge variant={live ? "default" : "outline"}>
                          {live ? "live" : "ended"}
                        </Badge>
                      </span>
                    </div>
                    {a.body && (
                      <p className="text-muted-foreground">{a.body}</p>
                    )}
                    {manage && live && (
                      <ReasonedAction
                        id={`end-announcement-${a.id}`}
                        label="End now"
                        onRun={(reason) =>
                          change.mutateAsync({
                            reason,
                            change: { kind: "end-announcement", id: a.id },
                          })
                        }
                      />
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {manage && (
            <div className="space-y-2 rounded-md border p-3">
              <p className="text-sm font-medium">New announcement</p>
              <Label htmlFor="announcement-title">Title</Label>
              <Input
                id="announcement-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Label htmlFor="announcement-body">Text</Label>
              <Textarea
                id="announcement-body"
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label htmlFor="announcement-level">Level</Label>
                  <select
                    id="announcement-level"
                    className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                    value={level}
                    onChange={(e) =>
                      setLevel(
                        e.target.value as "info" | "warning" | "critical",
                      )
                    }
                  >
                    <option value="info">info</option>
                    <option value="warning">warning</option>
                    <option value="critical">critical</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="announcement-ends">Ends (optional)</Label>
                  <Input
                    id="announcement-ends"
                    type="datetime-local"
                    value={endsAt}
                    onChange={(e) => setEndsAt(e.target.value)}
                  />
                </div>
              </div>
              <ReasonedAction
                label="Publish announcement"
                disabled={title.trim().length < 3}
                onRun={(reason) =>
                  change
                    .mutateAsync({
                      reason,
                      change: {
                        kind: "announce",
                        title: title.trim(),
                        body: body.trim(),
                        level,
                        endsAt: endsAt ? new Date(endsAt).toISOString() : null,
                      },
                    })
                    .then(() => {
                      setTitle("");
                      setBody("");
                      setEndsAt("");
                    })
                }
              />
            </div>
          )}
        </CardContent>
      </Card>

      {manage && (
        <Card className="lg:col-span-2" data-testid="platform-broadcast">
          <CardHeader>
            <CardTitle className="text-base">
              Broadcast a notification
            </CardTitle>
            <CardDescription>
              Lands in every active workspace&apos;s notifications (and their
              email, by their own preferences). It is sent once per workspace,
              even if the job is retried.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2 md:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="broadcast-title">Title</Label>
              <Input
                id="broadcast-title"
                value={bTitle}
                onChange={(e) => setBTitle(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="broadcast-body">Text</Label>
              <Input
                id="broadcast-body"
                value={bBody}
                onChange={(e) => setBBody(e.target.value)}
              />
            </div>
            <div className="md:col-span-2">
              <ReasonedAction
                label="Send to every workspace"
                destructive
                disabled={bTitle.trim().length < 3 || bBody.trim().length < 1}
                onRun={(reason) =>
                  broadcast
                    .mutateAsync({
                      title: bTitle.trim(),
                      body: bBody.trim(),
                      reason,
                    })
                    .then(() => {
                      setBTitle("");
                      setBBody("");
                    })
                }
              />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
