import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";

export type PlanKey = "starter" | "growth" | "pro";

export interface Plan {
  key: PlanKey;
  name: string;
  priceUsd: number;
  listingLimit: number;
  aiAddonUsd: number;
}

export interface BillingStatus {
  configured: boolean;
  plan: PlanKey | null;
  status: string;
  aiAddon: boolean;
  trialEndsAt: string;
  onTrial: boolean;
  paid: boolean;
  listingLimit: number;
  listingsUsed: number;
  canWrite: boolean;
  canUseAi: boolean;
  cancelAtPeriodEnd: boolean;
  currentPeriodEnd: string | null;
  hasCustomer: boolean;
  plans: Plan[];
}

export interface PlanChoice {
  plan: PlanKey;
  aiAddon: boolean;
}

interface Redirect {
  url: string;
}

export const billingKeys = {
  all: ["billing"] as const,
};

export function useBillingStatus(): UseQueryResult<BillingStatus> {
  return useQuery({
    queryKey: billingKeys.all,
    queryFn: async () => {
      const { data } = await apiClient.get<BillingStatus>("/billing");
      return data;
    },
  });
}

/** Stripe Checkout and the portal are Stripe-hosted: the server returns a
 * URL and the browser leaves the app. */
export function useStartCheckout() {
  return useMutation({
    mutationFn: async (choice: PlanChoice) => {
      const { data } = await apiClient.post<Redirect>("/billing/checkout", choice);
      return data.url;
    },
  });
}

export function useOpenBillingPortal() {
  return useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post<Redirect>("/billing/portal");
      return data.url;
    },
  });
}

function useStatusMutation<T>(fn: (input: T) => Promise<BillingStatus>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (status) => queryClient.setQueryData(billingKeys.all, status),
  });
}

export function useChangePlan() {
  return useStatusMutation(async (choice: PlanChoice) => {
    const { data } = await apiClient.post<BillingStatus>("/billing/change", choice);
    return data;
  });
}

/** Re-read the subscription on return from Checkout, so the new plan shows
 * before (or without) the webhook. */
export function useSyncBilling() {
  return useStatusMutation<void>(async () => {
    const { data } = await apiClient.post<BillingStatus>("/billing/sync");
    return data;
  });
}
