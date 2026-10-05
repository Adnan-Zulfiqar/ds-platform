import type { Metadata } from "next";

import { ProfileWorkspace } from "@/components/profile/profile-workspace";
import { PageHeader } from "@/components/ui/page-header";

export const metadata: Metadata = { title: "Profile" };

/** Settings → Profile. The `(protected)` layout guards it. */
export default function ProfileSettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader title="Profile" description="Your name, email address, and sign-in security." />
      <ProfileWorkspace />
    </div>
  );
}
