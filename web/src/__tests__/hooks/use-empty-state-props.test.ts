import { renderHook } from '@testing-library/react'
import { Inbox, Search } from 'lucide-react'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'

const empty = {
  title: 'No items',
  description: 'Create your first item to get started.',
} as const

const filtered = {
  title: 'No matching items',
  description: 'Adjust your filters.',
  action: { label: 'Clear filters', onClick: () => {} },
} as const

describe('useEmptyStateProps', () => {
  it('returns null when filteredCount > 0', () => {
    const { result } = renderHook(() =>
      useEmptyStateProps({
        filteredCount: 5,
        totalCount: 10,
        filterActive: true,
        empty,
        filtered,
      }),
    )
    expect(result.current).toBeNull()
  })

  it('returns the empty branch when totalCount is 0', () => {
    const { result } = renderHook(() =>
      useEmptyStateProps({
        filteredCount: 0,
        totalCount: 0,
        filterActive: false,
        icon: Inbox,
        empty,
        filtered,
      }),
    )
    expect(result.current).toEqual({
      icon: Inbox,
      title: empty.title,
      description: empty.description,
      action: undefined,
    })
  })

  it('returns the filtered branch when totalCount > 0 and filter is active', () => {
    const { result } = renderHook(() =>
      useEmptyStateProps({
        filteredCount: 0,
        totalCount: 12,
        filterActive: true,
        icon: Search,
        empty,
        filtered,
      }),
    )
    expect(result.current).toEqual({
      icon: Search,
      title: filtered.title,
      description: filtered.description,
      action: filtered.action,
    })
  })

  it('returns the empty branch when totalCount > 0 but filter is NOT active', () => {
    // Logical edge: filteredCount === 0 but filter inactive means the
    // pool is genuinely empty (or out-of-sync); treat as empty branch.
    const { result } = renderHook(() =>
      useEmptyStateProps({
        filteredCount: 0,
        totalCount: 0,
        filterActive: false,
        empty,
        filtered,
      }),
    )
    expect(result.current?.title).toBe(empty.title)
  })

  it('returns the same reference when inputs are stable across renders', () => {
    const { result, rerender } = renderHook(
      (input: Parameters<typeof useEmptyStateProps>[0]) => useEmptyStateProps(input),
      {
        initialProps: {
          filteredCount: 0,
          totalCount: 0,
          filterActive: false,
          empty,
          filtered,
        },
      },
    )
    const first = result.current
    rerender({
      filteredCount: 0,
      totalCount: 0,
      filterActive: false,
      empty,
      filtered,
    })
    expect(result.current).toBe(first)
  })
})
