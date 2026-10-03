import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page } from "@/types/api";

/**
 * Global pricing and shipping rules (M3A).
 *
 * Distinct from `services/pricing.ts`, which speaks to the older
 * `/pricing/rules` catalogue-repricing API. These are different endpoints with
 * different semantics — versioned, scoped rules with an audit trail — so they
 * get their own contracts rather than being forced into the older shapes.
 * Both remain until the older API is retired, which is not this milestone.
 *
 * **The backend is the pricing authority.** Nothing in this file computes a
 * price, a margin, or a landed cost. `usePreviewRule` sends the inputs and
 * renders what comes back; a second copy of the formula in TypeScript would
 * drift from the one that actually prices products, and the merchant would be
 * shown one number and charged another.
 *
 * Money crosses the wire as strings, not numbers. `19.99` is not representable
 * in IEEE-754, and a JSON number would silently round somebody's prices.
 */

// ---------------------------------------------------------------------------
// Contracts
// ---------------------------------------------------------------------------

export type RuleScope = "global" | "store" | "category" | "product" | "variant";

export type PricingStrategy =
  | "percentage_markup"
  | "fixed_markup"
  | "target_margin"
  | "hybrid"
  | "tiered";

export type PriceRounding = "none" | "ninety_nine" | "ninety_five" | "whole";

export type ShippingCostHandling =
  | "include_in_price"
  | "charge_separately"
  | "absorb_from_profit";

/**
 * There is deliberately no `preferred_carrier` member.
 *
 * "Prefer this carrier" is expressed by the `preferredCarriers` list, which
 * filters the quotes every strategy then chooses from — so it composes with
 * all four rather than being a fifth, mutually exclusive one. Adding a
 * strategy the backend cannot persist would produce a rule that saves and
 * then behaves as something else.
 */
export type ShippingSelectionStrategy =
  | "cheapest"
  | "cheapest_tracked"
  | "fastest"
  | "fastest_under_cost";

export type ShippingNoMatchBehaviour =
  | "needs_review"
  | "cheapest_available"
  | "block_publish";

export type RuleKind = "pricing" | "shipping";

/** The identifier a scope needs. `global` deliberately has none. */
export const SCOPE_IDENTIFIER: Record<RuleScope, keyof ScopeIdentifiers | null> = {
  global: null,
  store: "storeId",
  category: "categoryId",
  product: "productId",
  variant: "variantId",
};

export interface ScopeIdentifiers {
  storeId: string | null;
  categoryId: string | null;
  productId: string | null;
  variantId: string | null;
}

export interface PricingRule extends ScopeIdentifiers {
  id: string;
  name: string;
  scope: RuleScope;
  strategy: PricingStrategy;
  priority: number;
  markupPercent: string | null;
  markupFixed: string | null;
  marginPercent: string | null;
  minProfit: string | null;
  minProfitPerVariant: string | null;
  minPrice: string | null;
  maxPrice: string | null;
  dutyPercent: string | null;
  feesFixed: string | null;
  /** Track E2: marketplace/payment fee as a percentage of the selling price. */
  saleFeePercent: string | null;
  rounding: PriceRounding;
  compareAtPercent: string | null;
  shippingCostHandling: ShippingCostHandling;
  appliesToNewImports: boolean;
  tiers: Array<Record<string, unknown>>;
  currency: string | null;
  isActive: boolean;
  version: number;
  updatedAt: string;
}

export interface ShippingRule extends ScopeIdentifiers {
  id: string;
  name: string;
  scope: RuleScope;
  priority: number;
  destinationCountry: string | null;
  selectionStrategy: ShippingSelectionStrategy;
  maxDeliveryDays: number | null;
  maxShippingCost: string | null;
  trackingRequired: boolean;
  preferredCarriers: string[];
  blockedCarriers: string[];
  noMatchBehaviour: ShippingNoMatchBehaviour;
  isActive: boolean;
  version: number;
  updatedAt: string;
}

/** Create payloads. `expectedUpdatedAt` is absent — there is nothing to clash with. */
export type PricingRuleCreate = Omit<
  PricingRule,
  "id" | "version" | "updatedAt"
> & { note?: string };

export type ShippingRuleCreate = Omit<
  ShippingRule,
  "id" | "version" | "updatedAt"
> & { note?: string };

/**
 * Update payloads carry the concurrency token.
 *
 * Mandatory, not optional. A settings screen can sit open for an hour; without
 * the token the later save silently wins and the earlier editor's change
 * disappears with no trace that it ever existed.
 */
export type PricingRuleUpdate = PricingRuleCreate & { expectedUpdatedAt: string };
export type ShippingRuleUpdate = ShippingRuleCreate & { expectedUpdatedAt: string };

export interface RuleActivation {
  isActive: boolean;
  expectedUpdatedAt: string;
  note?: string;
}

export interface RuleVersion {
  id: string;
  ruleKind: RuleKind;
  ruleId: string;
  version: number;
  changedFields: string[];
  previousValues: Record<string, unknown>;
  newValues: Record<string, unknown>;
  snapshot: Record<string, unknown>;
  changedByUserId: string | null;
  note: string | null;
  isActive: boolean;
  productsAffected: number;
  createdAt: string;
}

export interface RuleResolution {
  ruleId: string | null;
  ruleName: string | null;
  scope: RuleScope | null;
  version: number | null;
  reason: string;
  overriddenRuleIds: string[];
}

export interface PreviewRequest {
  itemCost?: string | null;
  shippingCost?: string | null;
  currency?: string | null;
  ruleId?: string | null;
  productId?: string | null;
  variantId?: string | null;
  storeId?: string | null;
  categoryId?: string | null;
}

export interface PreviewResult {
  resolution: RuleResolution;
  itemCost: string;
  shippingCost: string;
  fees: string;
  landedCost: string;
  profitBasis: string;
  separateShippingCharge: string | null;
  priceBeforeRounding: string | null;
  proposedPrice: string | null;
  compareAtPrice: string | null;
  profit: string | null;
  markupPercent: string | null;
  marginPercent: string | null;
  rounding: PriceRounding;
  needsReview: boolean;
  reviewReasons: string[];
}

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

/**
 * Keyed by rule kind so invalidating pricing rules does not refetch shipping
 * rules, and neither refetches history for a rule nobody is looking at.
 */
export const globalRuleKeys = {
  all: ["global-rules"] as const,
  kind: (kind: RuleKind) => [...globalRuleKeys.all, kind] as const,
  lists: (kind: RuleKind) => [...globalRuleKeys.kind(kind), "list"] as const,
  list: (kind: RuleKind, query: ListQuery) =>
    [...globalRuleKeys.lists(kind), query] as const,
  detail: (kind: RuleKind, id: string) =>
    [...globalRuleKeys.kind(kind), "detail", id] as const,
  history: (kind: RuleKind, id: string, page: number) =>
    [...globalRuleKeys.kind(kind), "history", id, page] as const,
  resolve: (params: Record<string, string | null | undefined>) =>
    [...globalRuleKeys.all, "resolve", params] as const,
};

const BASE = "/global-rules";

// ---------------------------------------------------------------------------
// Pricing rules
// ---------------------------------------------------------------------------

export function usePricingRuleList(
  query: ListQuery = {},
): UseQueryResult<Page<PricingRule>> {
  return useQuery({
    queryKey: globalRuleKeys.list("pricing", query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<PricingRule>>(`${BASE}/pricing`, {
        params: query,
      });
      return data;
    },
  });
}

export function usePricingRule(
  id: string | null,
): UseQueryResult<PricingRule> {
  return useQuery({
    queryKey: globalRuleKeys.detail("pricing", id ?? ""),
    queryFn: async () => {
      const { data } = await apiClient.get<PricingRule>(`${BASE}/pricing/${id}`);
      return data;
    },
    // Without this a null id would fire a request to `/pricing/null`.
    enabled: Boolean(id),
  });
}

export function useShippingRuleList(
  query: ListQuery = {},
): UseQueryResult<Page<ShippingRule>> {
  return useQuery({
    queryKey: globalRuleKeys.list("shipping", query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<ShippingRule>>(`${BASE}/shipping`, {
        params: query,
      });
      return data;
    },
  });
}

export function useShippingRule(
  id: string | null,
): UseQueryResult<ShippingRule> {
  return useQuery({
    queryKey: globalRuleKeys.detail("shipping", id ?? ""),
    queryFn: async () => {
      const { data } = await apiClient.get<ShippingRule>(`${BASE}/shipping/${id}`);
      return data;
    },
    enabled: Boolean(id),
  });
}

/**
 * Invalidate exactly what changed.
 *
 * One rule kind's lists and that rule's detail and history — not
 * `globalRuleKeys.all`, which would refetch the other kind's list and every
 * open history page for no reason.
 */
function invalidateRule(
  queryClient: ReturnType<typeof useQueryClient>,
  kind: RuleKind,
  id?: string,
): Promise<unknown> {
  // Returned, not fired and forgotten: a mutation's onSuccess that returns a
  // promise holds `mutateAsync` until it settles. The rule dialogs close when
  // `mutateAsync` resolves, so without this a saved rule was missing from the
  // list until the refetch landed — visible to merchants on a slow request,
  // and the cause of CI flakes in global-rules.spec.ts (N-5).
  const pending: Promise<unknown>[] = [
    queryClient.invalidateQueries({ queryKey: globalRuleKeys.lists(kind) }),
  ];
  if (id) {
    pending.push(
      queryClient.invalidateQueries({ queryKey: globalRuleKeys.detail(kind, id) }),
      queryClient.invalidateQueries({
        queryKey: [...globalRuleKeys.kind(kind), "history", id],
      }),
    );
  }
  return Promise.all(pending);
}

export function useCreatePricingRule(): UseMutationResult<
  PricingRule,
  Error,
  PricingRuleCreate
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PricingRuleCreate) => {
      const { data } = await apiClient.post<PricingRule>(`${BASE}/pricing`, payload);
      return data;
    },
    onSuccess: (rule) => invalidateRule(queryClient, "pricing", rule.id),
  });
}

export function useUpdatePricingRule(
  id: string,
): UseMutationResult<PricingRule, Error, PricingRuleUpdate> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PricingRuleUpdate) => {
      const { data } = await apiClient.patch<PricingRule>(
        `${BASE}/pricing/${id}`,
        payload,
      );
      return data;
    },
    onSuccess: (rule) => invalidateRule(queryClient, "pricing", rule.id),
  });
}

export function useSetPricingRuleActive(
  id: string,
): UseMutationResult<PricingRule, Error, RuleActivation> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: RuleActivation) => {
      const { data } = await apiClient.post<PricingRule>(
        `${BASE}/pricing/${id}/activation`,
        payload,
      );
      return data;
    },
    onSuccess: (rule) => invalidateRule(queryClient, "pricing", rule.id),
  });
}

export function useCreateShippingRule(): UseMutationResult<
  ShippingRule,
  Error,
  ShippingRuleCreate
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ShippingRuleCreate) => {
      const { data } = await apiClient.post<ShippingRule>(`${BASE}/shipping`, payload);
      return data;
    },
    onSuccess: (rule) => invalidateRule(queryClient, "shipping", rule.id),
  });
}

export function useUpdateShippingRule(
  id: string,
): UseMutationResult<ShippingRule, Error, ShippingRuleUpdate> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ShippingRuleUpdate) => {
      const { data } = await apiClient.patch<ShippingRule>(
        `${BASE}/shipping/${id}`,
        payload,
      );
      return data;
    },
    onSuccess: (rule) => invalidateRule(queryClient, "shipping", rule.id),
  });
}

export function useSetShippingRuleActive(
  id: string,
): UseMutationResult<ShippingRule, Error, RuleActivation> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: RuleActivation) => {
      const { data } = await apiClient.post<ShippingRule>(
        `${BASE}/shipping/${id}/activation`,
        payload,
      );
      return data;
    },
    onSuccess: (rule) => invalidateRule(queryClient, "shipping", rule.id),
  });
}

// ---------------------------------------------------------------------------
// History, resolution, preview
// ---------------------------------------------------------------------------

export function useRuleHistory(
  kind: RuleKind,
  id: string | null,
  page = 1,
  size = 10,
): UseQueryResult<Page<RuleVersion>> {
  return useQuery({
    queryKey: globalRuleKeys.history(kind, id ?? "", page),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<RuleVersion>>(
        `${BASE}/${kind}/${id}/history`,
        { params: { page, size } },
      );
      return data;
    },
    enabled: Boolean(id),
    // History is append-only: a page already fetched cannot change, so
    // re-requesting it on every focus is pure waste.
    staleTime: 30_000,
  });
}

export function useResolveRule(
  params: Pick<PreviewRequest, "productId" | "variantId" | "storeId" | "categoryId">,
  enabled = true,
): UseQueryResult<RuleResolution> {
  return useQuery({
    queryKey: globalRuleKeys.resolve(params as Record<string, string | null | undefined>),
    queryFn: async () => {
      const { data } = await apiClient.get<RuleResolution>(`${BASE}/resolve`, {
        params,
      });
      return data;
    },
    enabled,
  });
}

// ---------------------------------------------------------------------------
// Target lookup, draft impact, and applications
// ---------------------------------------------------------------------------

export type TargetKind = "product" | "variant" | "category" | "store";

export interface RuleTarget {
  id: string;
  label: string;
  sublabel: string | null;
}

/**
 * Search selectable rule targets by name.
 *
 * `enabled` is what keeps a closed combobox silent: without it every scope
 * picker on the page would fetch on mount, four times over, for a list nobody
 * has opened.
 */
export function useRuleTargets(
  kind: TargetKind,
  search: string,
  options: { enabled?: boolean; productId?: string | null } = {},
): UseQueryResult<Page<RuleTarget>> {
  const { enabled = true, productId = null } = options;
  return useQuery({
    queryKey: [...globalRuleKeys.all, "targets", kind, search, productId] as const,
    queryFn: async () => {
      const { data } = await apiClient.get<Page<RuleTarget>>(`${BASE}/targets/${kind}`, {
        params: { search: search || undefined, size: 20, productId: productId || undefined },
      });
      return data;
    },
    enabled,
    // A label for a given id does not change between keystrokes.
    staleTime: 30_000,
  });
}

export interface ImpactVariant {
  variantId: string;
  label: string | null;
  currentPrice: string | null;
  proposedPrice: string | null;
  landedCost: string;
  profit: string | null;
  markupPercent: string | null;
  marginPercent: string | null;
  needsReview: boolean;
  reviewReasons: string[];
}

export interface ImpactItem {
  productId: string;
  title: string;
  currency: string | null;
  currentPrice: string | null;
  itemCost: string;
  supplierShippingCost: string;
  fees: string;
  landedCost: string;
  proposedPrice: string | null;
  compareAtPrice: string | null;
  profit: string | null;
  markupPercent: string | null;
  marginPercent: string | null;
  pricingRuleId: string | null;
  pricingRuleVersion: number | null;
  pricingRuleScope: RuleScope | null;
  ruleReason: string;
  shippingRuleId: string | null;
  shippingRuleVersion: number | null;
  shippingExplanation: string | null;
  published: boolean;
  canApply: boolean;
  needsReview: boolean;
  reviewReasons: string[];
  variants: ImpactVariant[];
}

export interface ImpactPage {
  items: ImpactItem[];
  total: number;
  page: number;
  size: number;
  applicableCount: number;
  reviewCount: number;
  publishedCount: number;
  /** What "select all matching" would cover, counted server-side. */
  selectableTotal: number;
  /** Uncapped match count, so a truncated selection can be said out loud. */
  matchingTotal: number;
  maxApplicationProducts: number;
  applicationBatchSize: number;
}

export interface ImpactQuery {
  page?: number;
  size?: number;
  search?: string;
  needsReviewOnly?: boolean;
  safeOnly?: boolean;
}

export function useDraftImpact(query: ImpactQuery): UseQueryResult<ImpactPage> {
  return useQuery({
    queryKey: [...globalRuleKeys.all, "impact", query] as const,
    queryFn: async () => {
      const { data } = await apiClient.get<ImpactPage>(`${BASE}/drafts/impact`, {
        params: {
          page: query.page ?? 1,
          size: query.size ?? 25,
          search: query.search || undefined,
          needsReviewOnly: query.needsReviewOnly || undefined,
          safeOnly: query.safeOnly || undefined,
        },
      });
      return data;
    },
  });
}

export type ApplicationStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export type ApplicationOutcome =
  | "applied"
  | "skipped"
  | "needs_review"
  | "stale"
  | "published"
  | "failed";

export const TERMINAL_STATUSES: readonly ApplicationStatus[] = [
  "completed",
  "partial",
  "failed",
  "cancelled",
];

export interface ApplicationItem {
  productId: string | null;
  variantId: string | null;
  outcome: ApplicationOutcome;
  previousPrice: string | null;
  newPrice: string | null;
  landedCost: string | null;
  appliedRuleVersion: number | null;
  reviewReasons: string[];
  message: string | null;
}

export interface Application {
  id: string;
  status: ApplicationStatus;
  idempotencyKey: string;
  heartbeatAt: string | null;
  recoveryCount: number;
  finishedAt: string | null;
  totalCount: number;
  processedCount: number;
  appliedCount: number;
  skippedCount: number;
  reviewCount: number;
  failedCount: number;
  failureReason: string | null;
  items: ApplicationItem[];
}

export interface ApplyPayload {
  idempotencyKey: string;
  productIds?: string[];
  selectionFilter?: {
    search?: string | null;
    needsReviewOnly?: boolean;
    safeOnly?: boolean;
  };
  expectedRuleId?: string | null;
  expectedRuleVersion?: number | null;
}

export function useApplyToDrafts(): UseMutationResult<Application, Error, ApplyPayload> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ApplyPayload) => {
      const { data } = await apiClient.post<Application>(`${BASE}/drafts/apply`, payload);
      return data;
    },
    onSuccess: () => {
      // The impact figures are stale the moment prices start moving.
      void queryClient.invalidateQueries({
        queryKey: [...globalRuleKeys.all, "impact"],
      });
    },
  });
}

/**
 * Poll one application until it reaches a terminal state.
 *
 * The interval backs off as a run gets longer, and stops entirely once the
 * status can no longer change — a fixed-interval poll that never stops is how
 * a status screen left open overnight becomes a denial-of-service on your own
 * API.
 */
export function useApplication(
  applicationId: string | null,
  options: { poll?: boolean } = {},
): UseQueryResult<Application> {
  const { poll = true } = options;
  return useQuery({
    queryKey: [...globalRuleKeys.all, "application", applicationId ?? ""] as const,
    queryFn: async () => {
      const { data } = await apiClient.get<Application>(
        `${BASE}/applications/${applicationId}`,
      );
      return data;
    },
    enabled: Boolean(applicationId),
    refetchInterval: (query) => {
      if (!poll) return false;
      const current = query.state.data as Application | undefined;
      if (current && TERMINAL_STATUSES.includes(current.status)) return false;
      // 1s while it is young, easing to 5s. A batch takes seconds, so a
      // faster poll buys nothing but load.
      const elapsed = Date.now() - (query.state.dataUpdatedAt || Date.now());
      return elapsed > 30_000 ? 5_000 : 1_500;
    },
    refetchIntervalInBackground: false,
  });
}

export function useCancelApplication(
  applicationId: string,
): UseMutationResult<Application, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<Application>(
        `${BASE}/applications/${applicationId}/cancel`,
        {},
      );
      return data;
    },
    onSuccess: (application) => {
      queryClient.setQueryData(
        [...globalRuleKeys.all, "application", applicationId],
        application,
      );
    },
  });
}

/**
 * The live calculator.
 *
 * A mutation rather than a query despite being read-only, because it is driven
 * by typing: `useMutation` gives an explicit "run it now" call, whereas a query
 * keyed on the inputs would cache a result per keystroke and refetch on focus.
 * The endpoint writes nothing — it prices a transient product that is never
 * added to the session.
 */
export function usePreviewRule(): UseMutationResult<
  PreviewResult,
  Error,
  PreviewRequest
> {
  return useMutation({
    mutationFn: async (payload: PreviewRequest) => {
      const { data } = await apiClient.post<PreviewResult>(`${BASE}/preview`, payload);
      return data;
    },
  });
}
