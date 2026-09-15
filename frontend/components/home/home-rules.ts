import type { AppNotification, NotificationKind } from "@/services/notifications";
import type {
  AliExpressStatus,
  EbayStatus,
  OrderStatistics,
  Product,
  ProductImportRecord,
  ProductWorkspaceCounts,
  ShopifyStatus,
} from "@/types/api";

/**
 * Pure derivations behind the Home page (UX-L2D-03).
 *
 * Nothing here fetches, guesses, or invents: every input is a response the
 * backend already returns, and every output is a deterministic function of
 * those inputs. The rules are written out in one place so a reviewer can
 * check them against the screen, and the tests exercise them through real
 * rendered states rather than by trusting this file.
 *
 * **What is deliberately absent.** A "ready to publish" count: readiness is a
 * per-draft server check (`POST /integrations/shopify/publish-readiness`)
 * with no aggregate or cached flag, so any count here would be a guess. It
 * stays a backend dependency. Revenue: the analytics figure sums
 * `Order.total_amount` across orders whose currencies differ, with no FX and
 * no currency in the payload, so no single currency label would be true —
 * it belongs on Reports, with that caveat, not on Home.
 */

export type ChannelState =
  | "connected"
  | "needs-attention"
  | "not-connected"
  | "not-configured";

export interface ChannelSummary {
  id: "shopify" | "aliexpress" | "ebay";
  label: string;
  state: ChannelState;
  /** One line a merchant can act on: what is true and, if needed, what to do. */
  detail: string;
  /**
   * Whether the channel can do its job right now — at least one Shopify
   * store connected, an unexpired AliExpress token. A store whose webhooks
   * need setting up is still usable for publishing; that problem belongs in
   * the attention list, not in the way of the next step.
   */
  usable: boolean;
}

export interface AttentionItem {
  id: string;
  /** Short, factual, present tense. */
  title: string;
  detail: string;
  href: string;
  action: string;
  severity: "error" | "warning";
}

export interface NextStep {
  /** Which rule produced this step — surfaced in tests, not in the UI. */
  rule: "connect-shopify" | "connect-aliexpress" | "import-first-product" | "continue-draft";
  title: string;
  detail: string;
  href: string;
  action: string;
}

const FAILURE_KINDS: ReadonlySet<NotificationKind> = new Set<NotificationKind>([
  "sync_failed",
  "webhook_failure",
  "task_failure",
  "automation_failed",
]);

export const NOTIFICATION_KIND_LABEL: Record<NotificationKind, string> = {
  import_completed: "Import completed",
  sync_failed: "Sync failed",
  inventory_changed: "Inventory changed",
  price_changed: "Price changed",
  order_imported: "Order imported",
  shipment_updated: "Shipment updated",
  webhook_failure: "Webhook failure",
  task_failure: "Task failed",
  automation_completed: "Automation completed",
  automation_failed: "Automation failed",
  info: "Update",
};

export function connectedShopifyCount(status: ShopifyStatus | undefined): number {
  return status?.connections.filter((c) => c.status === "connected").length ?? 0;
}

export function summariseChannels(input: {
  shopify?: ShopifyStatus;
  aliexpress?: AliExpressStatus;
  ebay?: EbayStatus;
}): ChannelSummary[] {
  const out: ChannelSummary[] = [];

  if (input.shopify) {
    const connected = connectedShopifyCount(input.shopify);
    const degraded = input.shopify.connections.filter(
      (c) => c.status === "connected" && c.webhookHealth === "degraded",
    ).length;
    const broken = input.shopify.connections.filter((c) => c.status !== "connected").length;
    if (!input.shopify.configured) {
      out.push({
        id: "shopify",
        label: "Shopify",
        state: "not-configured",
        detail: "Shopify is not configured on this server.",
        usable: false,
      });
    } else if (connected === 0) {
      out.push({
        id: "shopify",
        label: "Shopify",
        state: broken > 0 ? "needs-attention" : "not-connected",
        detail:
          broken > 0
            ? `${broken} ${broken === 1 ? "store needs" : "stores need"} reconnecting.`
            : "No store connected. Connect one to publish products.",
        usable: false,
      });
    } else if (degraded > 0 || broken > 0) {
      out.push({
        id: "shopify",
        label: "Shopify",
        state: "needs-attention",
        detail:
          degraded > 0
            ? `${connected} connected · ${degraded} ${degraded === 1 ? "store needs" : "stores need"} webhook setup.`
            : `${connected} connected · ${broken} ${broken === 1 ? "store needs" : "stores need"} reconnecting.`,
        usable: true,
      });
    } else {
      out.push({
        id: "shopify",
        label: "Shopify",
        state: "connected",
        detail: `${connected} ${connected === 1 ? "store" : "stores"} connected.`,
        usable: true,
      });
    }
  }

  if (input.aliexpress) {
    const { connected, connection } = input.aliexpress;
    if (connected) {
      out.push({
        id: "aliexpress",
        label: "AliExpress",
        state: "connected",
        detail: "Connected. Supplier products can be imported.",
        usable: true,
      });
    } else if (connection) {
      out.push({
        id: "aliexpress",
        label: "AliExpress",
        state: "needs-attention",
        detail: connection.isTokenExpired
          ? "Authorization expired. Reconnect to keep importing and syncing."
          : (connection.lastError ?? "Connection needs attention. Reconnect to continue."),
        usable: false,
      });
    } else {
      out.push({
        id: "aliexpress",
        label: "AliExpress",
        state: "not-connected",
        detail: "Not connected. Connect to import supplier products.",
        usable: false,
      });
    }
  }

  if (input.ebay) {
    if (!input.ebay.configured) {
      out.push({
        id: "ebay",
        label: "eBay",
        state: "not-configured",
        detail: "eBay is not configured on this server.",
        usable: false,
      });
    } else if (input.ebay.connected) {
      out.push({
        id: "ebay",
        label: "eBay",
        state: "connected",
        detail: input.ebay.connection?.ebayUsername
          ? `Connected as ${input.ebay.connection.ebayUsername}.`
          : "Connected.",
        usable: true,
      });
    } else {
      out.push({
        id: "ebay",
        label: "eBay",
        state: "not-connected",
        detail: "Not connected.",
        usable: false,
      });
    }
  }

  return out;
}

/**
 * Everything on Home that a merchant should deal with, most severe first.
 *
 * Each item comes from one endpoint and links to the page that can fix it.
 * "Not connected" is never an attention item — for a new workspace it is the
 * next step, not a fault.
 */
export function deriveAttentionItems(input: {
  channels: ChannelSummary[];
  recentImports?: ProductImportRecord[];
  recentDrafts?: Product[];
  orderStatistics?: OrderStatistics;
  notifications?: AppNotification[];
}): AttentionItem[] {
  const items: AttentionItem[] = [];

  for (const channel of input.channels) {
    if (channel.state === "needs-attention") {
      items.push({
        id: `channel-${channel.id}`,
        title: `${channel.label} needs attention`,
        detail: channel.detail,
        href: "/settings/integrations",
        action: "Open Integrations",
        severity: channel.id === "shopify" && channel.detail.includes("webhook") ? "warning" : "error",
      });
    }
  }

  const failedImports = (input.recentImports ?? []).filter((r) => r.status === "failed");
  if (failedImports.length > 0) {
    const first = failedImports[0];
    items.push({
      id: "failed-imports",
      title:
        failedImports.length === 1
          ? "An import failed"
          : `${failedImports.length} recent imports failed`,
      detail:
        first?.errorMessage ??
        (first?.errorCode ? `Error code: ${first.errorCode}` : "Open Import history to retry."),
      href: "/imports/history",
      action: "Review imports",
      severity: "error",
    });
  }

  const aiFailed = (input.recentDrafts ?? []).filter((d) => d.aiStatus === "failed");
  if (aiFailed.length > 0) {
    const first = aiFailed[0];
    items.push({
      id: "ai-failed",
      title:
        aiFailed.length === 1
          ? "AI optimisation failed for a recent draft"
          : `AI optimisation failed for ${aiFailed.length} recent drafts`,
      detail: first ? first.title : "",
      href: first ? `/drafts/${first.id}` : "/drafts",
      action: "Open draft",
      severity: "warning",
    });
  }

  const failedSyncs = input.orderStatistics?.failedSyncsLast7Days ?? 0;
  if (failedSyncs > 0) {
    items.push({
      id: "order-sync",
      title:
        failedSyncs === 1
          ? "An order sync failed this week"
          : `${failedSyncs} order syncs failed this week`,
      detail: "Orders may be missing or out of date until the next successful sync.",
      href: "/orders",
      action: "Open Orders",
      severity: "warning",
    });
  }

  const unreadFailures = (input.notifications ?? []).filter(
    (n) => !n.isRead && FAILURE_KINDS.has(n.kind),
  );
  for (const n of unreadFailures) {
    // Skip failures already represented by a structured item above.
    if (n.kind === "sync_failed" && failedSyncs > 0) continue;
    items.push({
      id: `notification-${n.id}`,
      title: n.title,
      detail: n.body,
      href: n.href ?? "/notifications",
      action: n.href ? "Open" : "View notifications",
      severity: "warning",
    });
  }

  return items.sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1));
}

/**
 * The single next action. Rules run in order; the first that applies wins:
 *
 * 1. No usable Shopify store → connect one (nothing can be published).
 * 2. AliExpress not usable → connect or reconnect it (nothing can be imported).
 * 3. No drafts → import the first product.
 * 4. Otherwise → continue the most recently edited draft.
 *
 * Returns `null` while the inputs it needs are still loading; the UI shows a
 * skeleton rather than a wrong answer.
 */
export function deriveNextStep(input: {
  channels: ChannelSummary[];
  counts?: ProductWorkspaceCounts;
  recentDrafts?: Product[];
}): NextStep | null {
  const shopify = input.channels.find((c) => c.id === "shopify");
  const aliexpress = input.channels.find((c) => c.id === "aliexpress");
  if (!shopify || !aliexpress || !input.counts) return null;

  if (!shopify.usable) {
    return {
      rule: "connect-shopify",
      title: shopify.state === "needs-attention" ? "Repair your Shopify connection" : "Connect your Shopify store",
      detail: "Products can only be published to a connected store.",
      href: "/settings/integrations",
      action: shopify.state === "needs-attention" ? "Open Integrations" : "Connect Shopify",
    };
  }
  if (!aliexpress.usable) {
    return {
      rule: "connect-aliexpress",
      title:
        aliexpress.state === "needs-attention" ? "Reconnect AliExpress" : "Connect AliExpress",
      detail: "Supplier products are imported through your AliExpress account.",
      href: "/settings/integrations",
      action: aliexpress.state === "needs-attention" ? "Open Integrations" : "Connect AliExpress",
    };
  }
  if (input.counts.drafts === 0) {
    return {
      rule: "import-first-product",
      title: "Import your first product",
      detail: "Paste an AliExpress product ID or URL to create a draft you can review and publish.",
      href: "/drafts",
      action: "Go to Drafts",
    };
  }
  const latest = input.recentDrafts?.[0];
  return {
    rule: "continue-draft",
    title: latest ? "Continue editing your latest draft" : "Review your drafts",
    detail: latest ? latest.title : `${input.counts.drafts} drafts are waiting for review.`,
    href: latest ? `/drafts/${latest.id}` : "/drafts",
    action: latest ? "Open draft" : "Go to Drafts",
  };
}

/**
 * A workspace with nothing in it yet: no drafts, no products, and no channel
 * connected. Home then shows the three-step setup instead of empty lists.
 * Unknown while the counts or channel statuses are still loading.
 */
export function isEmptyWorkspace(input: {
  counts?: ProductWorkspaceCounts;
  channels: ChannelSummary[];
}): boolean | undefined {
  const shopify = input.channels.find((c) => c.id === "shopify");
  const aliexpress = input.channels.find((c) => c.id === "aliexpress");
  if (!input.counts || !shopify || !aliexpress) return undefined;
  const anyConnected = [shopify, aliexpress].some((c) => c.usable);
  return input.counts.drafts === 0 && input.counts.products === 0 && !anyConnected;
}

/** "3 min ago", "2 h ago", "4 d ago" — relative to `now`, in the viewer's locale. */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((then - now) / 1000);
  const abs = Math.abs(seconds);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "narrow" });
  if (abs < 60) return rtf.format(seconds, "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86_400) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 86_400 * 30) return rtf.format(Math.round(seconds / 86_400), "day");
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}
