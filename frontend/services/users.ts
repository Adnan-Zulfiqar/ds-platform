import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ListQuery, Page, User } from "@/types/api";

/**
 * User data access.
 *
 * Components never call `apiClient` directly. Keeping the endpoint URL, the
 * query key, and the response type together in one module means a change to any
 * of the three is a single-file edit, and it makes cache invalidation reliable —
 * scattered inline key arrays inevitably drift and leave stale data on screen.
 */

/**
 * Hierarchical query keys.
 *
 * The nesting is what makes partial invalidation work: invalidating
 * `userKeys.lists()` clears every list regardless of its filters, while leaving
 * individual cached records intact.
 */
export const userKeys = {
  all: ["users"] as const,
  lists: () => [...userKeys.all, "list"] as const,
  list: (query: ListQuery) => [...userKeys.lists(), query] as const,
  details: () => [...userKeys.all, "detail"] as const,
  detail: (id: string) => [...userKeys.details(), id] as const,
};

async function fetchUsers(query: ListQuery): Promise<Page<User>> {
  const { data } = await apiClient.get<Page<User>>("/users", { params: query });
  return data;
}

async function fetchUser(id: string): Promise<User> {
  const { data } = await apiClient.get<User>(`/users/${id}`);
  return data;
}

export function useUsers(query: ListQuery = {}): UseQueryResult<Page<User>> {
  return useQuery({
    queryKey: userKeys.list(query),
    queryFn: () => fetchUsers(query),
  });
}

export function useUser(id: string | undefined): UseQueryResult<User> {
  return useQuery({
    queryKey: userKeys.detail(id ?? ""),
    queryFn: () => fetchUser(id as string),
    // Do not fire a request for an undefined id — which happens on first render
    // of a detail page while the route parameter is still resolving.
    enabled: Boolean(id),
  });
}

/** Track E4: open team invitations. The link secret is never returned. */
export interface Invitation {
  id: string;
  email: string;
  role: "admin" | "member" | "viewer";
  expiresAt: string;
  createdAt: string;
}

const invitationKeys = [...userKeys.all, "invitations"] as const;

export function useInvitations(enabled: boolean): UseQueryResult<Invitation[]> {
  return useQuery({
    queryKey: invitationKeys,
    queryFn: async () => {
      const { data } = await apiClient.get<Invitation[]>("/users/invitations");
      return data;
    },
    enabled,
  });
}

export function useInviteMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { email: string; role: Invitation["role"] }) => {
      const { data } = await apiClient.post<Invitation>("/users/invitations", payload);
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: invitationKeys });
    },
  });
}

export function useRevokeInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/users/invitations/${id}`);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: invitationKeys });
    },
  });
}
