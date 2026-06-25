import { useMemo } from 'react'
import { useOrgChartPrefs } from '@/stores/org-chart-prefs'

export interface OrgChartCollapseResult {
  collapsedDepts: Set<string>
  toggleDeptCollapsed: (deptId: string) => void
}

/**
 * Per-department collapse state, backed by the backend-sourced org-chart-prefs
 * store (``org_chart.collapsed_departments``). The dashboard is a pure API
 * consumer with no client-side copy; the store hydrates on mount and persists
 * every toggle through the settings API.
 */
export function useOrgChartCollapse(): OrgChartCollapseResult {
  const collapsedList = useOrgChartPrefs((s) => s.collapsedDepartments)
  const toggleDeptCollapsed = useOrgChartPrefs((s) => s.toggleCollapsedDepartment)
  const collapsedDepts = useMemo(() => new Set(collapsedList), [collapsedList])
  return { collapsedDepts, toggleDeptCollapsed }
}
