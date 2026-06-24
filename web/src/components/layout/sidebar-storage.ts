/** React hook for the sidebar's user-collapsed preference.
 *
 * The preference is backend-owned (``dashboard.sidebar_collapsed``); the
 * dashboard is a pure API consumer with no client-side copy. This wraps the
 * dashboard-prefs store, which hydrates from the backend on mount and persists
 * every change through the settings API.
 */

import { useDashboardPrefs } from '@/stores/dashboard-prefs'

/**
 * Subscribe a component to the sidebar collapsed-state preference.
 *
 * Returns ``[collapsed, setCollapsed]`` backed by the backend-sourced
 * dashboard-prefs store.
 */
export function useCollapsedState(): readonly [boolean, (value: boolean) => void] {
  const collapsed = useDashboardPrefs((s) => s.sidebarCollapsed)
  const setCollapsed = useDashboardPrefs((s) => s.setSidebarCollapsed)
  return [collapsed, setCollapsed]
}
