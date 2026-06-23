import { useCallback, useEffect, useMemo, useState } from 'react'
import { useEscalationsStore } from '@/stores/escalations'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import type { ConflictType, EscalationResponse, EscalationStatus } from '@/api/types/escalations'

export type PriorityBucket = 'critical' | 'high' | 'standard'
export type SortKey = 'priority' | 'created' | 'conflict_type'

/** Page window size for the cursor-mode pagination control. */
export const ESCALATION_PAGE_SIZE = 20

/**
 * Conflict-type buckets surfaced as the "priority" filter; the data
 * model has no explicit priority field, so we group by conflict domain.
 */
const PRIORITY_BUCKET_TYPES: Record<PriorityBucket, readonly ConflictType[]> = {
  critical: ['architecture'],
  high: ['implementation', 'priority'],
  standard: ['resource', 'process', 'other'],
}

export const PRIORITY_OPTIONS: ReadonlyArray<{ value: PriorityBucket | 'all'; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'standard', label: 'Standard' },
]

export const SORT_OPTIONS: ReadonlyArray<{ value: SortKey; label: string }> = [
  { value: 'created', label: 'Newest' },
  { value: 'priority', label: 'Priority' },
  { value: 'conflict_type', label: 'Conflict type' },
]

export const STATUS_OPTIONS: ReadonlyArray<{ value: EscalationStatus | 'all'; label: string }> = [
  { value: 'pending', label: 'Pending' },
  { value: 'decided', label: 'Decided' },
  { value: 'expired', label: 'Expired' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'all', label: 'All' },
]

function priorityRank(type: ConflictType): number {
  if (PRIORITY_BUCKET_TYPES.critical.includes(type)) return 0
  if (PRIORITY_BUCKET_TYPES.high.includes(type)) return 1
  return 2
}

function createdDesc(a: EscalationResponse, b: EscalationResponse): number {
  return new Date(b.escalation.created_at).getTime() - new Date(a.escalation.created_at).getTime()
}

function byPriority(a: EscalationResponse, b: EscalationResponse): number {
  const ra = priorityRank(a.escalation.conflict.type)
  const rb = priorityRank(b.escalation.conflict.type)
  return ra !== rb ? ra - rb : createdDesc(a, b)
}

function filterByPriority(list: readonly EscalationResponse[], filter: PriorityBucket | 'all'): EscalationResponse[] {
  if (filter === 'all') return [...list]
  return list.filter((row) => PRIORITY_BUCKET_TYPES[filter].includes(row.escalation.conflict.type))
}

function sortEscalations(list: EscalationResponse[], sortKey: SortKey): EscalationResponse[] {
  if (sortKey === 'priority') return [...list].sort(byPriority)
  if (sortKey === 'conflict_type') {
    return [...list].sort((a, b) => a.escalation.conflict.type.localeCompare(b.escalation.conflict.type))
  }
  return [...list].sort(createdDesc)
}

/**
 * Cursor-mode paging over the accumulated, filtered escalation list.
 *
 * Navigating past the loaded window pulls the next cursor batch (the
 * slice fills in once it resolves); navigating past the end with nothing
 * more to load is a no-op so the control cannot strand the operator on an
 * empty page. `resetPage` is called by the filter handlers when the
 * filtered/sorted basis changes.
 */
function useEscalationPaging(
  visibleEscalations: EscalationResponse[],
  hasMore: boolean,
  fetchMore: () => unknown,
): {
  page: number
  pagedEscalations: EscalationResponse[]
  loadPage: (page: number) => void
  resetPage: () => void
} {
  const [page, setPage] = useState(1)
  const pagedEscalations = useMemo(
    () => visibleEscalations.slice((page - 1) * ESCALATION_PAGE_SIZE, page * ESCALATION_PAGE_SIZE),
    [visibleEscalations, page],
  )
  const loadedPageCount = Math.max(1, Math.ceil(visibleEscalations.length / ESCALATION_PAGE_SIZE))
  const loadPage = useCallback(
    (target: number) => {
      const next = Math.max(1, target)
      if (next > loadedPageCount) {
        if (!hasMore) return
        void fetchMore()
      }
      setPage(next)
    },
    [loadedPageCount, hasMore, fetchMore],
  )
  const resetPage = useCallback(() => setPage(1), [])
  return { page, pagedEscalations, loadPage, resetPage }
}

type EmptyProps = ReturnType<typeof useEmptyStateProps>

function applyFilteredEmptyTitle(base: EmptyProps, filterActive: boolean, isEmpty: boolean): EmptyProps {
  if (base === null || !filterActive || !isEmpty) return base
  if (base.title === 'No escalations match your filters') return base
  return {
    ...base,
    title: 'No escalations match your filters',
    description: 'Adjust the status or priority filter above to see more escalations.',
  }
}

function useEscalationEmptyState(
  visibleCount: number,
  totalCount: number,
  filterActive: boolean,
): EmptyProps {
  const base = useEmptyStateProps({
    filteredCount: visibleCount,
    totalCount,
    filterActive,
    empty: {
      title: 'No escalations',
      description: 'Conflicts that the autonomous resolvers cannot decide land here for human review.',
    },
    filtered: {
      title: 'No escalations match your filters',
      description: 'Adjust the status or priority filter above to see more escalations.',
    },
  })
  return applyFilteredEmptyTitle(base, filterActive, visibleCount === 0)
}

export interface EscalationQueue {
  escalations: readonly EscalationResponse[]
  visibleEscalations: EscalationResponse[]
  pagedEscalations: EscalationResponse[]
  page: number
  pageSize: number
  loading: boolean
  loadingMore: boolean
  error: string | null
  hasMore: boolean
  statusFilter: EscalationStatus | null
  priorityFilter: PriorityBucket | 'all'
  sortKey: SortKey
  selectedId: string | null
  emptyStateProps: EmptyProps
  setSelectedId: (id: string | null) => void
  handleStatusChange: (value: string) => void
  handlePriorityChange: (value: string) => void
  handleSortChange: (value: string) => void
  retry: () => void
  loadPage: (page: number) => void
}

export function useEscalationQueue(): EscalationQueue {
  const escalations = useEscalationsStore((s) => s.escalations)
  const loading = useEscalationsStore((s) => s.loading)
  const loadingMore = useEscalationsStore((s) => s.loadingMore)
  const error = useEscalationsStore((s) => s.error)
  const hasMore = useEscalationsStore((s) => s.hasMore)
  const statusFilter = useEscalationsStore((s) => s.statusFilter)
  const fetchEscalations = useEscalationsStore((s) => s.fetchEscalations)
  const fetchMoreEscalations = useEscalationsStore((s) => s.fetchMoreEscalations)
  const setStatusFilter = useEscalationsStore((s) => s.setStatusFilter)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [priorityFilter, setPriorityFilter] = useState<PriorityBucket | 'all'>('all')
  const [sortKey, setSortKey] = useState<SortKey>('created')

  useEffect(() => {
    void fetchEscalations()
  }, [fetchEscalations])

  // Client-side priority filter + sort on top of the server-side status
  // filter, so the operator can narrow / re-order without a round-trip.
  const visibleEscalations = useMemo(
    () => sortEscalations(filterByPriority(escalations, priorityFilter), sortKey),
    [escalations, priorityFilter, sortKey],
  )

  const { page, pagedEscalations, loadPage, resetPage } = useEscalationPaging(
    visibleEscalations,
    hasMore,
    fetchMoreEscalations,
  )

  const filterActive = statusFilter != null || priorityFilter !== 'all'
  const emptyStateProps = useEscalationEmptyState(
    visibleEscalations.length,
    escalations.length,
    filterActive,
  )

  // Each filter / sort change re-windows from the first page so the
  // operator never lands on an out-of-range page after narrowing the list.
  const handleStatusChange = useCallback(
    (value: string) => {
      resetPage()
      if (value === 'all') {
        setStatusFilter(null)
        return
      }
      if (STATUS_OPTIONS.some((o) => o.value === value)) setStatusFilter(value as EscalationStatus)
    },
    [setStatusFilter, resetPage],
  )
  const handlePriorityChange = useCallback(
    (value: string) => {
      resetPage()
      if (PRIORITY_OPTIONS.some((o) => o.value === value)) setPriorityFilter(value as PriorityBucket | 'all')
    },
    [resetPage],
  )
  const handleSortChange = useCallback(
    (value: string) => {
      resetPage()
      if (SORT_OPTIONS.some((o) => o.value === value)) setSortKey(value as SortKey)
    },
    [resetPage],
  )

  return {
    escalations,
    visibleEscalations,
    pagedEscalations,
    page,
    pageSize: ESCALATION_PAGE_SIZE,
    loading,
    loadingMore,
    error,
    hasMore,
    statusFilter,
    priorityFilter,
    sortKey,
    selectedId,
    emptyStateProps,
    setSelectedId,
    handleStatusChange,
    handlePriorityChange,
    handleSortChange,
    retry: () => void fetchEscalations(),
    loadPage,
  }
}
