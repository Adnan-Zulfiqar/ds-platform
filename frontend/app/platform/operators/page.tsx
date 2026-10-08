"use client";

import {
  PlatformPageHeader,
  RequirePermission,
  usePlatformAccess,
} from "@/components/platform/platform-shell";
import { PlatformOperators } from "@/components/platform/platform-operators";

export default function PlatformOperatorsPage() {
  const { me, can } = usePlatformAccess();
  return (
    <RequirePermission permission="operators.read">
      <PlatformPageHeader
        title="Operators"
        description="Who can use this console, and their sessions."
      />
      <PlatformOperators selfId={me.id} canManage={can("operators.manage")} />
    </RequirePermission>
  );
}
