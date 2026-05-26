import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

export type TabValue = 'general' | 'agents' | 'departments'

export const isTabValue = (value: string): value is TabValue =>
  value === 'general' || value === 'agents' || value === 'departments'

export interface OrgEditTabResult {
  activeTab: TabValue
  handleTabChange: (value: TabValue) => void
}

/** Read/write the active org-edit tab via the `?tab=` search param. */
export function useOrgEditTab(): OrgEditTabResult {
  const [searchParams, setSearchParams] = useSearchParams()
  const rawTab = searchParams.get('tab') ?? 'general'
  const activeTab: TabValue = isTabValue(rawTab) ? rawTab : 'general'

  const handleTabChange = useCallback(
    (value: TabValue) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (value === 'general') {
          next.delete('tab')
        } else {
          next.set('tab', value)
        }
        return next
      })
    },
    [setSearchParams],
  )

  return { activeTab, handleTabChange }
}
