import { PlatformWorkspacePage } from "@/components/platform/platform-workspace-page";

export default async function PlatformWorkspaceRoute({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  return <PlatformWorkspacePage tenantId={tenantId} />;
}
