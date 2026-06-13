import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  approveRequest,
  listRequests,
  rejectRequest,
  scopeRequest,
  type ClientRequest,
  type RequestStatus,
} from '@/api/endpoints/clients'
import { useCapabilities } from '@/hooks/useCapabilities'
import { createLogger } from '@/lib/logger'

const log = createLogger('RequestQueuePage')

// ``typeof null === 'object'`` in JS, so the null guard must come first
// or the ``'description' in requirement`` check throws at runtime.
function requirementMatchesQuery(requirement: unknown, query: string): boolean {
  return (
    requirement !== null
    && typeof requirement === 'object'
    && 'description' in requirement
    && typeof requirement.description === 'string'
    && requirement.description.toLowerCase().includes(query)
  )
}

function matchesRequest(
  r: ClientRequest,
  statusFilter: RequestStatus | 'all',
  query: string,
): boolean {
  if (statusFilter !== 'all' && r.status !== statusFilter) return false
  if (query === '') return true
  return (
    r.request_id.toLowerCase().includes(query)
    || r.client_id.toLowerCase().includes(query)
    || requirementMatchesQuery(r.requirement, query)
  )
}

interface RequestActions {
  pending: Record<string, boolean>
  handleScope: (id: string) => void
  handleApprove: (id: string) => void
  handleReject: (id: string) => void
}

function useRequestActions(
  refresh: () => Promise<void>,
  setError: (error: string | null) => void,
): RequestActions {
  const [pending, setPending] = useState<Record<string, boolean>>({})
  // Synchronous in-flight guard. ``setPending`` only commits on the next
  // render, so a rapid second click would re-enter `run` before the
  // `pending` state reflects the first; mutating this ref atomically at
  // the start (and clearing it in `finally`) closes that re-entrancy
  // race. Keeping the guard in a ref rather than `pending` state also
  // means `run` does not depend on `pending`, so it and the three action
  // handlers stay stable and do not invalidate the RequestCard memo on
  // every transition.
  const inFlightRef = useRef<Record<string, boolean>>({})

  const run = useCallback(
    async (
      requestId: string,
      action: () => Promise<unknown>,
      errorMsg: string,
      logEvent: string,
    ) => {
      if (inFlightRef.current[requestId]) return
      inFlightRef.current[requestId] = true
      setPending((prev) => ({ ...prev, [requestId]: true }))
      try {
        await action()
        await refresh()
      } catch (err) {
        log.error(logEvent, err)
        setError(errorMsg)
      } finally {
        inFlightRef.current[requestId] = false
        setPending((prev) => ({ ...prev, [requestId]: false }))
      }
    },
    [refresh, setError],
  )

  const handleScope = useCallback(
    (id: string) => {
      void run(id, () => scopeRequest(id, { notes: 'Scoped from dashboard' }), 'Failed to scope request.', 'scope_request_failed')
    },
    [run],
  )
  const handleApprove = useCallback(
    (id: string) => {
      void run(id, () => approveRequest(id), 'Failed to approve request.', 'approve_request_failed')
    },
    [run],
  )
  const handleReject = useCallback(
    (id: string) => {
      void run(id, () => rejectRequest(id, 'Rejected from dashboard'), 'Failed to reject request.', 'reject_request_failed')
    },
    [run],
  )

  return { pending, handleScope, handleApprove, handleReject }
}

export interface RequestQueueState {
  capabilities: ReturnType<typeof useCapabilities>['capabilities']
  capLoading: boolean
  capError: string | null
  requests: readonly ClientRequest[]
  loading: boolean
  error: string | null
  searchQuery: string
  setSearchQuery: (value: string) => void
  statusFilter: RequestStatus | 'all'
  setStatusFilter: (value: RequestStatus | 'all') => void
  filteredRequests: readonly ClientRequest[]
  pending: Record<string, boolean>
  handleScope: (id: string) => void
  handleApprove: (id: string) => void
  handleReject: (id: string) => void
}

export function useRequestQueue(): RequestQueueState {
  const { capabilities, loading: capLoading, error: capError } = useCapabilities()
  const [requests, setRequests] = useState<readonly ClientRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<RequestStatus | 'all'>('all')

  const refresh = useCallback(async () => {
    try {
      const result = await listRequests({ limit: 200 })
      setRequests(result.data)
      setError(null)
    } catch (err) {
      log.error('list_requests_failed', err)
      setError('Failed to load request queue.')
    } finally {
      setLoading(false)
    }
  }, [])

  const actions = useRequestActions(refresh, setError)

  // Capability-gated effect: skip the network call entirely when the
  // requests subsystem is not configured (backend route 404s otherwise).
  useEffect(() => {
    if (capLoading) return
    if (!capabilities.requests) {
      queueMicrotask(() => setLoading(false))
      return
    }
    void refresh()
  }, [refresh, capLoading, capabilities.requests])

  const filteredRequests = useMemo(() => {
    const trimmed = searchQuery.trim().toLowerCase()
    return requests.filter((r) => matchesRequest(r, statusFilter, trimmed))
  }, [requests, searchQuery, statusFilter])

  return {
    capabilities, capLoading, capError, requests, loading, error, searchQuery, setSearchQuery,
    statusFilter, setStatusFilter, filteredRequests, ...actions,
  }
}
