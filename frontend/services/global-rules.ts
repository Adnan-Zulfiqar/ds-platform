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
) {
  void queryClient.invalidateQueries({ queryKey: globalRuleKeys.lists(kind) });
  if (id) {
    void queryClient.invalidateQueries({
      queryKey: globalRuleKeys.detail(kind, id),
    });
    void queryClient.invalidateQueries({
      queryKey: [...globalRuleKeys.kind(kind), "history", id],
    });
  }
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
