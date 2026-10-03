import {
  Bell,
  ChevronRight,
  CreditCard,
  Plug,
  SlidersHorizontal,
  User,
  Users,
} from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import type { ComponentType } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Settings" };

interface SettingsSection {
  href: string;
  label: string;
  description: string;
  icon: ComponentType<{ className?: string }>;
  available: boolean;
}

/**
 * Settings index.
 *
 * Phase 2 rendered a bare placeholder here. Phase 3 added a real settings
 * page — integrations — so this became a genuine index rather than a dead end.
 *
 * Sections that do not exist render as non-interactive cards rather than links,
 * for the same reason the sidebar does: primary navigation must never reach a
 * 404, and a disabled link that keyboard focus lands on and does nothing is
 * worse than one that was never interactive.
 */
const SECTIONS: readonly SettingsSection[] = [
  {
    href: "/settings/integrations",
    label: "Integrations",
    description: "Connect suppliers and sales channels.",
    icon: Plug,
    available: true,
  },
  {
    href: "/settings/global-rules",
    label: "Global Rules",
    description: "Pricing and shipping rules applied across your catalogue.",
    icon: SlidersHorizontal,
    available: true,
  },
  {
    href: "/settings/notifications",
    label: "Notifications",
    description: "Choose which notifications you also get by email.",
    icon: Bell,
    available: true,
  },
  {
    href: "/settings/profile",
    label: "Profile",
    description: "Your name, email address, and password.",
    icon: User,
    available: false,
  },
  {
    href: "/settings/team",
    label: "Team",
    description: "Invite colleagues and manage their roles.",
    icon: Users,
    available: false,
  },
  {
    href: "/settings/billing",
    label: "Billing",
    description: "Subscription plan and payment method.",
    icon: CreditCard,
    available: false,
  },
];

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Workspace, team, and integration configuration."
      />

      <div className="grid gap-4 sm:grid-cols-2">
        {SECTIONS.map((section) => {
          const Icon = section.icon;

          const inner = (
            <CardHeader className="flex-row items-start gap-4 space-y-0">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
                <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              </div>
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <CardTitle className="text-base">{section.label}</CardTitle>
                  {!section.available && <Badge variant="outline">Coming soon</Badge>}
                </div>
                <CardDescription>{section.description}</CardDescription>
              </div>
              {section.available && (
                <ChevronRight
                  className="h-4 w-4 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
              )}
            </CardHeader>
          );

          if (!section.available) {
            return (
              <Card key={section.href} aria-disabled="true" className="opacity-60">
                {inner}
              </Card>
            );
          }

          return (
            <Link
              key={section.href}
              href={section.href}
              className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <Card className={cn("h-full transition-colors hover:bg-accent/40")}>
                {inner}
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
