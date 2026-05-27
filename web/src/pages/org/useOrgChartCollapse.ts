import { useCallback, useEffect, useState } from 'react'
import { createLogger } from '@/lib/logger'

const log = createLogger('OrgChart')

const COLLAPSED_DEPTS_KEY = 'synthorg:orgchart:collapsed-depts'

function loadCollapsedDepts(): Set<string> {
  try {
    const stored = localStorage.getItem(COLLAPSED_DEPTS_KEY)
    if (!stored) return new Set()
    const parsed: unknown = JSON.parse(stored)
    if (
      !Array.isArray(parsed) ||
      !parsed.every((entry): entry is string => typeof entry === 'string')
    ) {
      log.warn('Discarding malformed collapsed-depts storage payload', { type: typeof parsed })
      return new Set()
    }
    return new Set<string>(parsed)
  } catch (err) {
    log.warn('Failed to load collapsed depts from localStorage:', err)
  }
  return new Set()
}

export interface OrgChartCollapseResult {
  collapsedDepts: Set<string>
  toggleDeptCollapsed: (deptId: string) => void
}

/** Per-department collapse state, persisted to localStorage. */
export function useOrgChartCollapse(): OrgChartCollapseResult {
  const [collapsedDepts, setCollapsedDepts] = useState<Set<string>>(() => new Set())

  // Hydrate from localStorage after mount so no storage read happens
  // during render (the read lives in loadCollapsedDepts).
  useEffect(() => {
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- one-time localStorage hydration on mount
    setCollapsedDepts(loadCollapsedDepts())
  }, [])

  const toggleDeptCollapsed = useCallback((deptId: string) => {
    setCollapsedDepts((prev) => {
      const next = new Set(prev)
      if (next.has(deptId)) next.delete(deptId)
      else next.add(deptId)
      try {
        localStorage.setItem(COLLAPSED_DEPTS_KEY, JSON.stringify([...next]))
      } catch (err) {
        log.warn('Failed to persist collapsed depts:', err)
      }
      return next
    })
  }, [])

  return { collapsedDepts, toggleDeptCollapsed }
}
