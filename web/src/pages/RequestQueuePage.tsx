import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Inbox } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'

import {
  approveRequest,
  listRequests,
  rejectRequest,
  scopeRequest,
  type ClientRequest,
  type RequestStatus,
} from '@/api/endpoints/clients'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
import { RequestCard } from '@/components/ui/request-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField } from '@/components/ui/select-field'
import { SkeletonCard } from '@/components/ui/skeleton'
import { useCapabilities } from '@/hooks/useCapabilities'
import { createLogger } from '@/lib/logger'

const log = createLogger('RequestQueuePage')

const STATUS_ORDER: readonly RequestStatus[] = [
  'submitted',
  'triaging',
  'scoping',
  'approved',
  'task_created',
  'cancelled',
]

const STATUS_LABELS: Record<RequestStatus, string> = {
  submitted: 'Submitted',
  triaging: 'Triaging',
  scoping: 'Scoping',
  approved: 'Approved',
  task_created: 'Task created',
  cancelled: 'Cancelled',
}

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
  // Read the live pending map through a ref so `run` does not depend on
  // `pending`; otherwise every pending transition re-creates run (and the
  // three action handlers below), needlessly invalidating RequestCard memo.
  const pendingRef = useRef(pending)
  pendingRef.current = pending

  const run = useCallback(
    async (
      requestId: string,
      action: () => Promise<unknown>,
      errorMsg: string,
      logEvent: string,
    ) => {
      if (pendingRef.current[requestId]) return
      setPending((prev) => ({ ...prev, [requestId]: true }))
      try {
        await action()
        await refresh()
      } catch (err) {
        log.error(logEvent, err)
        setError(errorMsg)
      } finally {
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

interface RequestQueueState {
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

function useRequestQueue(): RequestQueueState {
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

type RequestQueueFallback = 'cap-error' | 'not-configured' | 'loading' | null

function requestQueueFallback(
  capLoading: boolean,
  capError: string | null,
  requestsEnabled: boolean,
  loading: boolean,
  requestsLength: number,
): RequestQueueFallback {
  if (!capLoading && capError !== null) return 'cap-error'
  if (!capLoading && !requestsEnabled) return 'not-configured'
  if (capLoading || (loading && requestsLength === 0)) return 'loading'
  return null
}

function RequestQueueFallbackView({
  state,
  capError,
}: {
  state: Exclude<RequestQueueFallback, null>
  capError: string | null
}) {
  if (state === 'cap-error') {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Request Queue" />
        <ErrorBanner
          severity="error"
          title="Could not determine available features"
          description={capError ?? undefined}
        />
      </div>
    )
  }
  if (state === 'not-configured') {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Requests" />
        <EmptyState
          icon={Inbox}
          title="Requests not configured"
          description={
            'This deployment did not enable the client request facade. Configure it in your ' +
            'backend setup to start tracking incoming requests.'
          }
        />
      </div>
    )
  }
  return (
    <div className="space-y-section-gap">
      <ListHeader title="Request Queue" />
      <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-3">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    </div>
  )
}

function RequestQueueFilters({
  searchQuery,
  setSearchQuery,
  statusFilter,
  setStatusFilter,
}: {
  searchQuery: string
  setSearchQuery: (value: string) => void
  statusFilter: RequestStatus | 'all'
  setStatusFilter: (value: RequestStatus | 'all') => void
}) {
  return (
    <SearchFilterSort
      search={
        <SearchInput
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder="Search by request id, summary, or client"
          ariaLabel="Search requests"
        />
      }
      filters={
        <SelectField
          label="Status"
          value={statusFilter}
          onChange={(value) => setStatusFilter(value as RequestStatus | 'all')}
          options={[
            { value: 'all', label: 'All statuses' },
            ...STATUS_ORDER.map((s) => ({ value: s, label: STATUS_LABELS[s] })),
          ]}
        />
      }
    />
  )
}

function RequestQueueBoard({
  filteredRequests,
  pending,
  onScope,
  onApprove,
  onReject,
}: {
  filteredRequests: readonly ClientRequest[]
  pending: Record<string, boolean>
  onScope: (id: string) => void
  onApprove: (id: string) => void
  onReject: (id: string) => void
}) {
  const grouped = STATUS_ORDER.map((status) => ({
    status,
    entries: filteredRequests.filter((r) => r.status === status),
  }))
  return (
    <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 xl:grid-cols-3">
      {grouped.map(({ status, entries }) => (
        <SectionCard key={status} title={STATUS_LABELS[status]} icon={Inbox}>
          {entries.length === 0 ? (
            <EmptyState
              title="No entries"
              description={`Nothing in ${STATUS_LABELS[status].toLowerCase()} yet.`}
            />
          ) : (
            <ul className="space-y-2">
              {entries.map((request) => (
                <RequestCard
                  key={request.request_id}
                  request={request}
                  pending={pending}
                  onScope={onScope}
                  onApprove={onApprove}
                  onReject={onReject}
                />
              ))}
            </ul>
          )}
        </SectionCard>
      ))}
    </div>
  )
}

/**
 * Lightweight Kanban-style view of the client request lifecycle.
 *
 * Groups stored ``ClientRequest``s by status so operators can watch the
 * independent request state machine (SUBMITTED → TRIAGING → ... →
 * TASK_CREATED | CANCELLED) at a glance. Per-card actions only expose
 * the legal next transition for each status; the state machine is
 * stage-gated to prevent operators from skipping triage.
 */
export default function RequestQueuePage() {
  const q = useRequestQueue()
  const fallback = requestQueueFallback(
    q.capLoading,
    q.capError,
    q.capabilities.requests,
    q.loading,
    q.requests.length,
  )

  if (fallback) {
    return <RequestQueueFallbackView state={fallback} capError={q.capError} />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Request Queue"
        description="Submitted → Triaging → Scoping → Approved → Task created."
        count={q.requests.length}
      />

      {q.error && (
        <ErrorBanner severity="error" title="Could not load request queue" description={q.error} />
      )}

      {q.requests.length > 0 && (
        <RequestQueueFilters
          searchQuery={q.searchQuery}
          setSearchQuery={q.setSearchQuery}
          statusFilter={q.statusFilter}
          setStatusFilter={q.setStatusFilter}
        />
      )}

      {q.requests.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No requests yet"
          description="Submit a client request via POST /requests to start exercising the intake pipeline."
        />
      ) : (
        <RequestQueueBoard
          filteredRequests={q.filteredRequests}
          pending={q.pending}
          onScope={q.handleScope}
          onApprove={q.handleApprove}
          onReject={q.handleReject}
        />
      )}
    </div>
  )
}
