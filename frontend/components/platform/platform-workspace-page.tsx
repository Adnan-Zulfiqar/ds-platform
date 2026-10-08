"use client";

import { PlatformWorkspaceDetail } from "@/components/platform/platform-workspaces";

/** The workspace page and its tabs. Later phases add tabs here. */
export function PlatformWorkspacePage({ tenantId }: { tenantId: string }) {
  return <PlatformWorkspaceDetail tenantId={tenantId} />;
}
