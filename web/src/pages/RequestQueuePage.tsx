import { useCallback, useEffect, useState } from 'react'
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
import { SectionCard } from '@/components/ui/section-card'
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

/**
 * Lightweight Kanban-style view of the client request lifecycle.
 *
 * Groups stored ``ClientRequest``s by status so operators can watch
 * the independent request state machine (SUBMITTED → TRIAGING → ...
 * → TASK_CREATED | CANCELLED) at a glance.
 */
export default function RequestQueuePage() {
  const {
    capabilities,
    loading: capLoading,
    error: capError,
  } = useCapabilities()
  const [requests, setRequests] = useState<readonly ClientRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<Record<string, boolean>>({})

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

  const handleScope = useCallback(
    async (requestId: string) => {
      if (pending[requestId]) return
      setPending((prev) => ({ ...prev, [requestId]: true }))
      try {
        await scopeRequest(requestId, { notes: 'Scoped from dashboard' })
        await refresh()
      } catch (err) {
        log.error('scope_request_failed', err)
        setError('Failed to scope request.')
      } finally {
        setPending((prev) => ({ ...prev, [requestId]: false }))
      }
    },
    [refresh, pending],
  )

  const handleApprove = useCallback(
    async (requestId: string) => {
      if (pending[requestId]) return
      setPending((prev) => ({ ...prev, [requestId]: true }))
      try {
        await approveRequest(requestId)
        await refresh()
      } catch (err) {
        log.error('approve_request_failed', err)
        setError('Failed to approve request.')
      } finally {
        setPending((prev) => ({ ...prev, [requestId]: false }))
      }
    },
    [refresh, pending],
  )

  const handleReject = useCallback(
    async (requestId: string) => {
      if (pending[requestId]) return
      setPending((prev) => ({ ...prev, [requestId]: true }))
      try {
        await rejectRequest(requestId, 'Rejected from dashboard')
        await refresh()
      } catch (err) {
        log.error('reject_request_failed', err)
        setError('Failed to reject request.')
      } finally {
        setPending((prev) => ({ ...prev, [requestId]: false }))
      }
    },
    [refresh, pending],
  )

  // Capability-gated effect: skip the network call entirely when the
  // requests subsystem is not configured. Backend route is also not
  // registered (returns 404). The early-return path that renders the
  // EmptyState lives below all hooks so React's hook-order rules
  // stay satisfied across renders. While ``capLoading`` is true we
  // do nothing -- the skeleton branch below covers the in-flight
  // window so the page never flashes "No requests yet" against an
  // unconfigured deployment.
  useEffect(() => {
    if (capLoading) {
      return
    }
    if (!capabilities.requests) {
      // Defer the loading flip out of the same synchronous render
      // frame so eslint-react's set-state-in-effect rule stays
      // satisfied and React batches one render instead of two.
      queueMicrotask(() => setLoading(false))
      return
    }
    void refresh()
  }, [refresh, capLoading, capabilities.requests])

  if (!capLoading && capError !== null) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Request Queue" />
        <ErrorBanner
          severity="error"
          title="Could not determine available features"
          description={capError}
        />
      </div>
    )
  }

  if (!capLoading && !capabilities.requests) {
    return (
      <div className="space-y-section-gap">
        <ListHeader title="Requests" />
        <EmptyState
          icon={Inbox}
          title="Requests not configured"
          description={
            'This deployment did not enable the client request facade. ' +
            'Configure it in your backend setup to start tracking ' +
            'incoming requests.'
          }
        />
      </div>
    )
  }

  // Hold the skeleton until capabilities resolve AND the first refresh
  // either lands data or sets loading=false. This prevents a one-frame
  // "No requests yet" flash on an unconfigured-but-not-yet-resolved
  // deployment.
  if (capLoading || (loading && requests.length === 0)) {
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

  const grouped = STATUS_ORDER.map((status) => ({
    status,
    entries: requests.filter((r) => r.status === status),
  }))

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Request Queue" count={requests.length} />

      {error && (
        <ErrorBanner severity="error" title="Could not load request queue" description={error} />
      )}

      {requests.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No requests yet"
          description="Submit a client request via POST /requests to start exercising the intake pipeline."
        />
      ) : (
        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 xl:grid-cols-3">
          {grouped.map(({ status, entries }) => (
            <SectionCard key={status} title={STATUS_LABELS[status]} icon={Inbox}>
              {entries.length === 0 ? (
                <p className="text-sm text-text-secondary">No entries.</p>
              ) : (
                <ul className="space-y-2">
                  {entries.map((request) => (
                    <RequestCard
                      key={request.request_id}
                      request={request}
                      pending={pending}
                      onScope={(id) => void handleScope(id)}
                      onApprove={(id) => void handleApprove(id)}
                      onReject={(id) => void handleReject(id)}
                    />
                  ))}
                </ul>
              )}
            </SectionCard>
          ))}
        </div>
      )}
    </div>
  )
}
