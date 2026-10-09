import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";

/** Platform status for every merchant (D-019): maintenance and announcements
 * written by DropPilot operators. Public and tenant-free. */

export interface SystemAnnouncement {
  id: string;
  title: string;
  body: string;
  level: "info" | "warning" | "critical";
  endsAt: string | null;
}

export interface SystemStatus {
  maintenance: boolean;
  maintenanceMessage: string | null;
  announcements: SystemAnnouncement[];
}

export function useSystemStatus(): UseQueryResult<SystemStatus> {
  return useQuery({
    queryKey: ["system", "status"],
    queryFn: async () =>
      (await apiClient.get<SystemStatus>("/system/status")).data,
    // A banner a minute late is fine; asking every second is not.
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}
