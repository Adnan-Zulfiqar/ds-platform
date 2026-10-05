import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page } from "@/types/api";

export type AutomationAction =
  | "import_product"
  | "sync_inventory"
  | "update_pricing"
  | "refresh_orders"
  | "archive_completed_orders"
  | "retry_failed_jobs";

export type AutomationSchedule = "hourly" | "daily" | "weekly" | "manual";

export interface AutomationRule {
  id: string;
  name: string;
  action: AutomationAction;
  schedule: AutomationSchedule;
  storeId: string | null;
  config: Record<string, unknown>;
  isActive: boolean;
  lastRunAt: string | null;
  nextRunAt: string | null;
  consecutiveFailures: number;
  createdAt: string;
  updatedAt: string;
}

export interface AutomationRun {
  id: string;
  ruleId: string;
  status: string;
  trigger: string;
  summary: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
}

export interface AutomationRuleCreatePayload {
  name: string;
  action: AutomationAction;
  schedule?: AutomationSchedule;
  storeId?: string;
  config?: Record<string, unknown>;
  isActive?: boolean;
}

export const automationKeys = {
  all: ["automation"] as const,
  list: (query: ListQuery) => [...automationKeys.all, "list", query] as const,
  runs: (query: ListQuery) => [...automationKeys.all, "runs", query] as const,
};

/** `GET /automation/runs`: the run history, which had no screen. */
export function useAutomationRuns(query: ListQuery = {}): UseQueryResult<Page<AutomationRun>> {
  return useQuery({
    queryKey: automationKeys.runs(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<AutomationRun>>("/automation/runs", {
        params: query,
      });
      return data;
    },
  });
}

export function useAutomationRules(
  query: ListQuery = {},
): UseQueryResult<Page<AutomationRule>> {
  return useQuery({
    queryKey: automationKeys.list(query),
    queryFn: async () => {
      const { data } = await apiClient.get<Page<AutomationRule>>("/automation/rules", {
        params: query,
      });
      return data;
    },
  });
}

export function useCreateAutomationRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AutomationRuleCreatePayload) => {
      const { data } = await apiClient.post<AutomationRule>(
        "/automation/rules",
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.all });
    },
  });
}

export function useRunAutomation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (ruleId: string) => {
      const { data } = await apiClient.post<AutomationRun>(
        `/automation/rules/${ruleId}/run`,
      );
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: automationKeys.all });
    },
  });
}
