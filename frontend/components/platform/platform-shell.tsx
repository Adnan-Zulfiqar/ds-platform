"use client";

import {
  Activity,
  Building2,
  Cog,
  KeyRound,
  LayoutDashboard,
  ScrollText,
  Settings2,
  ShieldCheck,
  ToggleRight,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  createContext,
  useContext,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { ReauthProvider } from "@/components/platform/reauth-dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import {
  type PlatformMe,
  type PlatformPermission,
  roleLabel,
  usePlatformLogin,
  usePlatformLogout,
  usePlatformMe,
  usePlatformSignedIn,
} from "@/services/platform";

/**
 * The operator console's frame (Track E5d, D-018, D-019). Outside the tenant
 * app: no tenant session, no tenant navigation. On a deployment where the
 * panel is switched off the API answers 404, and the sign-in form says so.
 *
 * Navigation follows the permissions `/me` reports. That is a convenience
 * only: every route checks the same permission on the server.
 */

const AccessContext = createContext<PlatformMe | null>(null);

/** The signed-in operator. Only inside `<PlatformShell>`. */
export function usePlatformAccess(): {
  me: PlatformMe;
  can: (permission: PlatformPermission) => boolean;
} {
  const me = useContext(AccessContext);
  if (!me)
    throw new Error("usePlatformAccess must be used inside <PlatformShell>");
  return { me, can: (permission) => me.permissions.includes(permission) };
}

/** Renders its children only when the operator holds `permission`. */
export function RequirePermission({
  permission,
  children,
}: {
  permission: PlatformPermission;
  children: ReactNode;
}) {
  const { can } = usePlatformAccess();
  if (!can(permission)) {
    return (
      <Alert>
        <AlertDescription>
          Your role does not include this section.
        </AlertDescription>
      </Alert>
    );
  }
  return <>{children}</>;
}

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  needs: PlatformPermission | null;
}

const NAV: NavItem[] = [
  {
    href: "/platform",
    label: "Dashboard",
    icon: LayoutDashboard,
    needs: "dashboard.read",
  },
  {
    href: "/platform/workspaces",
    label: "Workspaces",
    icon: Building2,
    needs: "tenants.read",
  },
  { href: "/platform/jobs", label: "Jobs", icon: Cog, needs: "jobs.read" },
  {
    href: "/platform/feature-flags",
    label: "Feature switches",
    icon: ToggleRight,
    needs: "billing.read",
  },
  {
    href: "/platform/operators",
    label: "Operators",
    icon: ShieldCheck,
    needs: "operators.read",
  },
  {
    href: "/platform/audit",
    label: "Audit log",
    icon: ScrollText,
    needs: "audit.read",
  },
  {
    href: "/platform/settings",
    label: "Settings",
    icon: Settings2,
    needs: "dashboard.read",
  },
  {
    href: "/platform/sessions",
    label: "My sessions",
    icon: KeyRound,
    needs: null,
  },
];

export function PlatformShell({ children }: { children: ReactNode }) {
  const signedIn = usePlatformSignedIn();
  if (!signedIn) {
    return (
      <main
        className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8"
        id="main-content"
      >
        <Title />
        <SignIn />
      </main>
    );
  }
  return (
    <ReauthProvider>
      <SignedIn>{children}</SignedIn>
    </ReauthProvider>
  );
}

function Title() {
  return (
    <div>
      <h1 className="text-xl font-semibold">DropPilot platform</h1>
      <p className="text-sm text-muted-foreground">
        Operator console. Every action is audited.
      </p>
    </div>
  );
}

function SignedIn({ children }: { children: ReactNode }) {
  const me = usePlatformMe();
  const logout = usePlatformLogout();
  const pathname = usePathname();

  if (me.isError) {
    return (
      <main
        className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8"
        id="main-content"
      >
        <Title />
        <ErrorState
          title="Could not load your operator account"
          onRetry={() => void me.refetch()}
        />
      </main>
    );
  }
  if (!me.data) {
    return (
      <main
        className="mx-auto max-w-6xl space-y-6 p-4 sm:p-8"
        id="main-content"
      >
        <Title />
        <Skeleton className="h-64 w-full" />
      </main>
    );
  }
  const data = me.data;
  const items = NAV.filter(
    (n) => n.needs === null || data.permissions.includes(n.needs),
  );

  return (
    <AccessContext.Provider value={data}>
      <div className="min-h-screen lg:grid lg:grid-cols-[14rem_1fr]">
        <aside className="border-b bg-muted/30 p-4 lg:min-h-screen lg:border-b-0 lg:border-r">
          <div className="mb-4 flex items-center gap-2">
            <Activity className="h-5 w-5" aria-hidden />
            <span className="font-semibold">DropPilot platform</span>
          </div>
          <nav
            aria-label="Console sections"
            className="flex gap-1 overflow-x-auto lg:flex-col"
          >
            {items.map((item) => {
              const active =
                item.href === "/platform"
                  ? pathname === "/platform"
                  : (pathname?.startsWith(item.href) ?? false);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm",
                    active
                      ? "bg-primary/10 font-medium text-foreground"
                      : "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
                  )}
                >
                  <item.icon className="h-4 w-4" aria-hidden />
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </aside>
        <div className="min-w-0">
          <header className="flex flex-wrap items-center justify-end gap-3 border-b px-4 py-3 text-sm sm:px-8">
            <span
              className="text-muted-foreground"
              data-testid="platform-operator-email"
            >
              {data.email}
            </span>
            <Badge variant="outline" data-testid="platform-operator-role">
              {roleLabel(data.role)}
            </Badge>
            <Button
              size="sm"
              variant="outline"
              disabled={logout.isPending}
              onClick={() => logout.mutate()}
            >
              Sign out
            </Button>
          </header>
          <main className="space-y-6 p-4 sm:p-8" id="main-content">
            {children}
          </main>
        </div>
      </div>
    </AccessContext.Provider>
  );
}

function SignIn() {
  const login = usePlatformLogin();
  const [form, setForm] = useState({ email: "", password: "", code: "" });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    login.mutate(form, {
      onSettled: () => setForm((f) => ({ ...f, password: "", code: "" })),
    });
  }

  const error =
    login.error instanceof ApiError && login.error.status === 404
      ? "The platform console is not enabled for this network."
      : login.error
        ? "Sign-in failed. Check the email, password and one-time code."
        : null;

  return (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle className="text-base">Operator sign-in</CardTitle>
        <CardDescription>
          Password and the 6-digit code from your authenticator app.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-3" onSubmit={onSubmit} noValidate>
          <div className="space-y-1">
            <Label htmlFor="platform-email">Email</Label>
            <Input
              id="platform-email"
              type="email"
              autoComplete="username"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="platform-password">Password</Label>
            <Input
              id="platform-password"
              type="password"
              autoComplete="current-password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="platform-code">One-time code</Label>
            <Input
              id="platform-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={form.code}
              onChange={(e) =>
                setForm({ ...form, code: e.target.value.replace(/\D/g, "") })
              }
            />
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button
            type="submit"
            disabled={
              login.isPending ||
              !form.email ||
              !form.password ||
              form.code.length !== 6
            }
          >
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

/** A section heading used by every console page. */
export function PlatformPageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold">{title}</h1>
        {description && (
          <p className="text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions}
    </div>
  );
}
