"use client";

import { AttentionPanel } from "@/components/home/attention-panel";
import { CatalogueSummary } from "@/components/home/catalogue-summary";
import { ChannelStatus } from "@/components/home/channel-status";
import { EmptyWorkspace } from "@/components/home/empty-workspace";
import {
  deriveAttentionItems,
  deriveNextStep,
  isEmptyWorkspace,
  summariseChannels,
} from "@/components/home/home-rules";
import { BlockError, BlockSkeleton, HomeSection } from "@/components/home/home-section";
import { NextStepCard } from "@/components/home/next-step-card";
import { RecentActivity } from "@/components/home/recent-activity";
import { RecentDrafts } from "@/components/home/recent-drafts";
import { ImportProductDialog } from "@/components/products/import-product-dialog";
import { PageHeader } from "@/components/ui/page-header";
import { useAuth } from "@/providers/auth-provider";
import { useDrafts } from "@/services/drafts";
import {
  useAliExpressStatus,
  useEbayStatus,
  useShopifyStatus,
} from "@/services/integrations";
import { useNotifications } from "@/services/notifications";
import { useOrderStatistics } from "@/services/orders";
import { useProductImports, useProductWorkspaceCounts } from "@/services/products";

/**
 * Home — the merchant's operations page (UX-L2D-03).
 *
 * Answers, in this order: what needs attention, what to do next, how big the
 * catalogue is, what was being worked on, whether channels are healthy, and
 * what happened recently. Every block reads an endpoint that already exists;
 * the derivations live in `components/home/home-rules.ts`.
 *
 * Reporting (revenue, charts, period selection) lives on `/analytics`. It
 * used to be duplicated here as fourteen metric cards with revenue labelled
 * `USD` regardless of the workspace — Home now shows no monetary figure,
 * because the analytics revenue is a sum across orders in mixed currencies
 * with no currency in the payload, and no label would be true.
 *
 * **Layout.** One column up to `xl`; from 1280px the attention list, next
 * step and recent drafts take two thirds and the summaries a third. A
 * three-column split at 1024px left the rail too narrow to read.
 *
 * **Error isolation.** Each block owns its query. A block that fails shows a
 * local error with its own retry; the others render normally. Only the two
 * inputs the page cannot reason without — workspace counts and Shopify /
 * AliExpress status — gate the empty-workspace decision, and while they load
 * the page keeps its shape with skeletons rather than jumping.
 */
export default function HomePage() {
  const { identity } = useAuth();
  const firstName = identity?.user.firstName;

  const counts = useProductWorkspaceCounts();
  const shopify = useShopifyStatus();
  const aliexpress = useAliExpressStatus();
  const ebay = useEbayStatus();
  const recentDrafts = useDrafts({ size: 5, sortBy: "updated_at", sortDir: "desc" });
  const recentImports = useProductImports({ size: 10 });
  const orderStatistics = useOrderStatistics();
  const notifications = useNotifications({ page: 1, size: 5 });

  const channels = summariseChannels({
    shopify: shopify.data,
    aliexpress: aliexpress.data,
    ebay: ebay.data,
  });
  const channelsPending = shopify.isPending || aliexpress.isPending || ebay.isPending;
  const channelsError = shopify.isError || aliexpress.isError || ebay.isError;

  const empty = isEmptyWorkspace({ counts: counts.data, channels });

  const attention = deriveAttentionItems({
    channels,
    recentImports: recentImports.data?.items,
    recentDrafts: recentDrafts.data?.items,
    orderStatistics: orderStatistics.data,
    notifications: notifications.data?.items,
  });
  const attentionPending =
    channelsPending ||
    recentImports.isPending ||
    recentDrafts.isPending ||
    orderStatistics.isPending ||
    notifications.isPending;

  const nextStep = deriveNextStep({
    channels,
    counts: counts.data,
    recentDrafts: recentDrafts.data?.items,
  });

  const retryChannels = () => {
    void shopify.refetch();
    void aliexpress.refetch();
    void ebay.refetch();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={firstName ? `Welcome back, ${firstName}` : "Home"}
        description={
          identity
            ? `What needs your attention at ${identity.tenant.name}, and what to do next.`
            : "What needs your attention, and what to do next."
        }
        // The setup checklist carries its own import step; two identical
        // primary buttons on an otherwise empty page would compete.
        actions={empty === true ? undefined : <ImportProductDialog />}
      />

      {empty === undefined ? (
        // Counts and channel status decide which Home this is; until they
        // arrive, hold the space rather than draw one layout and swap it.
        counts.isError || shopify.isError || aliexpress.isError ? (
          <BlockError
            what="your workspace"
            onRetry={() => {
              void counts.refetch();
              retryChannels();
            }}
          />
        ) : (
          <div className="grid gap-6 xl:grid-cols-3" data-testid="home-loading">
            <div className="space-y-6 xl:col-span-2">
              <BlockSkeleton rows={1} rowHeight="h-12" />
              <BlockSkeleton rows={1} rowHeight="h-24" />
              <BlockSkeleton rows={3} />
            </div>
            <div className="space-y-6">
              <BlockSkeleton rows={3} rowHeight="h-16" />
              <BlockSkeleton rows={3} rowHeight="h-14" />
            </div>
          </div>
        )
      ) : empty ? (
        <EmptyWorkspace channels={channels} />
      ) : (
        <div className="grid gap-6 xl:grid-cols-3">
          <div className="space-y-6 xl:col-span-2">
            <HomeSection id="home-attention" title="Needs attention">
              <AttentionPanel items={attention} pending={attentionPending} />
            </HomeSection>

            <NextStepCard step={nextStep} />

            <HomeSection
              id="home-continue"
              title="Continue working"
              link={{ href: "/drafts", label: "All drafts" }}
            >
              {recentDrafts.isPending ? (
                <BlockSkeleton rows={3} />
              ) : recentDrafts.isError ? (
                <BlockError what="your recent drafts" onRetry={() => void recentDrafts.refetch()} />
              ) : (
                <RecentDrafts drafts={recentDrafts.data.items} />
              )}
            </HomeSection>
          </div>

          <div className="space-y-6">
            <HomeSection id="home-catalogue" title="Your catalogue">
              {counts.isError ? (
                <BlockError what="your catalogue counts" onRetry={() => void counts.refetch()} />
              ) : (
                <CatalogueSummary
                  drafts={counts.data?.drafts}
                  products={counts.data?.products}
                  ordersAwaitingFulfilment={
                    orderStatistics.isError
                      ? null
                      : orderStatistics.data?.pendingFulfillment
                  }
                />
              )}
            </HomeSection>

            <HomeSection
              id="home-channels"
              title="Channels"
              link={{ href: "/settings/integrations", label: "Integrations" }}
            >
              {channelsError && channels.length === 0 ? (
                <BlockError what="channel status" onRetry={retryChannels} />
              ) : channelsPending && channels.length === 0 ? (
                <BlockSkeleton rows={3} rowHeight="h-14" />
              ) : (
                <>
                  <ChannelStatus channels={channels} />
                  {channelsError && (
                    <BlockError what="one channel's status" onRetry={retryChannels} />
                  )}
                </>
              )}
            </HomeSection>

            <HomeSection
              id="home-activity"
              title="Recent activity"
              link={{ href: "/notifications", label: "View all" }}
            >
              {notifications.isPending ? (
                <BlockSkeleton rows={3} rowHeight="h-14" />
              ) : notifications.isError ? (
                <BlockError what="recent activity" onRetry={() => void notifications.refetch()} />
              ) : (
                <RecentActivity notifications={notifications.data.items} />
              )}
            </HomeSection>
          </div>
        </div>
      )}
    </div>
  );
}
