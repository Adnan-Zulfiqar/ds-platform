import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Client-side UI state.
 *
 * **Scope discipline matters here.** Zustand holds ephemeral UI state only —
 * sidebar open, active modal. Server data belongs in React Query, which already
 * solves caching, refetching, and invalidation. Copying API responses into a
 * Zustand store is the single most common way a React codebase ends up with two
 * competing sources of truth that drift apart.
 */
interface UIState {
  /** Desktop sidebar collapsed to icons. Persisted — it is a user preference. */
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;

  /** Mobile sidebar drawer. Not persisted — it must never restore open. */
  mobileSidebarOpen: boolean;
  setMobileSidebarOpen: (open: boolean) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      toggleSidebar: () =>
        set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

      mobileSidebarOpen: false,
      setMobileSidebarOpen: (open) => set({ mobileSidebarOpen: open }),
    }),
    {
      name: "droppilot-ui",
      // Persist only the durable preference. Without this filter the mobile
      // drawer state would be written to localStorage and the drawer would
      // reappear open on the next visit.
      partialize: (state) => ({ sidebarCollapsed: state.sidebarCollapsed }),
    },
  ),
);
