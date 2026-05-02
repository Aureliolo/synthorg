/**
 * Derive `<EmptyState>` props from a list page's data + filter state.
 *
 * Most list pages render two distinct empty states:
 *  - "no data ever" -- the resource pool is empty
 *  - "no data after filter" -- the pool has items but the active
 *    filter excluded all of them; the empty state usually offers
 *    a "Clear filters" action
 *
 * Per-page implementations of this discriminator drift in subtle
 * ways (different copy, different action wiring, sometimes computed
 * inside JSX vs `useMemo`). This hook centralises the derivation
 * so the choice between the two states is uniform.
 *
 * The hook intentionally returns `null` when the page DOES have
 * items to render -- callers branch on the result rather than
 * conditionally render a default `<EmptyState>` themselves.
 */

import { useMemo } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { EmptyStateAction, EmptyStateProps } from '@/components/ui/empty-state'

export interface UseEmptyStatePropsInput {
  /** Visible item count after filtering. */
  filteredCount: number
  /** Total item count before filtering (== `filteredCount` when no filter is active). */
  totalCount: number
  /** Whether any filter is currently applied. */
  filterActive: boolean
  /** Icon shared across both unfiltered and filtered empty states. */
  icon?: LucideIcon
  /** Copy + action for the "no data ever" branch. */
  empty: {
    title: string
    description?: string
    action?: EmptyStateAction
  }
  /** Copy + action for the "no data after filter" branch. */
  filtered: {
    title: string
    description?: string
    action?: EmptyStateAction
  }
}

/**
 * Returns `EmptyStateProps` for whichever branch applies, or `null`
 * when the list has items to render and no empty state is needed.
 */
export function useEmptyStateProps(
  input: UseEmptyStatePropsInput,
): EmptyStateProps | null {
  const { filteredCount, totalCount, filterActive, icon, empty, filtered } = input
  return useMemo(() => {
    if (filteredCount > 0) return null
    if (filterActive && totalCount > 0) {
      return {
        icon,
        title: filtered.title,
        description: filtered.description,
        action: filtered.action,
      }
    }
    return {
      icon,
      title: empty.title,
      description: empty.description,
      action: empty.action,
    }
  }, [filteredCount, totalCount, filterActive, icon, empty, filtered])
}
