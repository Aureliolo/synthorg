import { useCallback, useEffect, useState } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { useSsrfViolationsStore } from '@/stores/ssrf-violations'
import type { EmptyStateProps } from '@/components/ui/empty-state'
import type { ResolveSsrfViolationRequest, SsrfViolationDTO } from '@/api/types'
import type { SsrfViolationStatus } from '@/api/types/enum-values.gen'
import { hasPrivilegedRole } from '@/utils/roles'

export type StatusFilterValue = SsrfViolationStatus | 'all'

export const SSRF_STATUS_OPTIONS: ReadonlyArray<{ value: StatusFilterValue; label: string }> = [
  { value: 'pending', label: 'Pending' },
  { value: 'allowed', label: 'Allowed' },
  { value: 'denied', label: 'Denied' },
  { value: 'all', label: 'All' },
]

/** A pending allow/deny decision awaiting confirmation. */
export interface PendingResolution {
  violation: SsrfViolationDTO
  status: ResolveSsrfViolationRequest['status']
}

export interface SsrfViolationsController {
  violations: readonly SsrfViolationDTO[]
  loading: boolean
  loadingMore: boolean
  error: string | null
  hasMore: boolean
  canManage: boolean
  statusFilter: StatusFilterValue
  resolvingId: string | null
  emptyStateProps: EmptyStateProps | null
  pending: PendingResolution | null
  handleStatusChange: (value: StatusFilterValue) => void
  requestResolve: (violation: SsrfViolationDTO, status: ResolveSsrfViolationRequest['status']) => void
  cancelResolve: () => void
  confirmResolve: () => Promise<boolean>
  loadMore: () => void
  retry: () => void
}

export function useSsrfViolations(): SsrfViolationsController {
  const { userRole } = useAuth()
  const canManage = hasPrivilegedRole(userRole)

  const violations = useSsrfViolationsStore((s) => s.violations)
  const loading = useSsrfViolationsStore((s) => s.loading)
  const loadingMore = useSsrfViolationsStore((s) => s.loadingMore)
  const error = useSsrfViolationsStore((s) => s.error)
  const hasMore = useSsrfViolationsStore((s) => s.hasMore)
  const storeStatus = useSsrfViolationsStore((s) => s.statusFilter)
  const resolvingId = useSsrfViolationsStore((s) => s.resolvingId)
  const fetchViolations = useSsrfViolationsStore((s) => s.fetchViolations)
  const fetchMore = useSsrfViolationsStore((s) => s.fetchMoreViolations)
  const setStatusFilter = useSsrfViolationsStore((s) => s.setStatusFilter)
  const resolveViolation = useSsrfViolationsStore((s) => s.resolveViolation)

  const [pending, setPending] = useState<PendingResolution | null>(null)

  useEffect(() => {
    void fetchViolations()
  }, [fetchViolations])

  const statusFilter: StatusFilterValue = storeStatus ?? 'all'
  const emptyStateProps = useEmptyStateProps({
    filteredCount: violations.length,
    totalCount: violations.length,
    filterActive: storeStatus !== null,
    empty: {
      title: 'No SSRF violations',
      description: 'Outbound URLs blocked by the egress guard will appear here for review.',
    },
    filtered: {
      title: 'No violations match this filter',
      description: 'Try a different status filter to see more violations.',
    },
  })

  const handleStatusChange = useCallback(
    (value: StatusFilterValue) => setStatusFilter(value === 'all' ? null : value),
    [setStatusFilter],
  )

  const confirmResolve = useCallback(async (): Promise<boolean> => {
    if (pending === null) return false
    // Returning `false` keeps the confirm dialog open so the operator can
    // retry; the dialog closes itself on a non-`false` (success) return.
    return resolveViolation(pending.violation.id, pending.status)
  }, [pending, resolveViolation])

  return {
    violations,
    loading,
    loadingMore,
    error,
    hasMore,
    canManage,
    statusFilter,
    resolvingId,
    emptyStateProps,
    pending,
    handleStatusChange,
    requestResolve: (violation, status) => setPending({ violation, status }),
    cancelResolve: () => setPending(null),
    confirmResolve,
    loadMore: () => void fetchMore(),
    retry: () => void fetchViolations(),
  }
}
