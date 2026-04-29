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
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
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
  const { capabilities, loading: capLoading } = useCapabilities()
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
  // stay satisfied across renders.
  useEffect(() => {
    if (capLoading || !capabilities.requests) {
      // Defer the loading flip out of the same synchronous render
      // frame so eslint-react's set-state-in-effect rule stays
      // satisfied and React batches one render instead of two.
      queueMicrotask(() => setLoading(false))
      return
    }
    void refresh()
  }, [refresh, capLoading, capabilities.requests])

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

  if (loading && requests.length === 0) {
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
                    <li
                      key={request.request_id}
                      className="space-y-2 rounded-md border border-border bg-card-hover p-card text-sm"
                    >
                      <div className="font-medium text-foreground">
                        {request.requirement.title}
                      </div>
                      <div className="text-xs text-text-secondary">
                        {request.client_id} · {request.request_id.slice(0, 8)}
                      </div>
                      {(request.status === 'submitted' ||
                        request.status === 'triaging' ||
                        request.status === 'scoping') && (
                        <div className="flex flex-wrap gap-2 pt-1">
                          {(request.status === 'submitted' ||
                            request.status === 'triaging') && (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={!!pending[request.request_id]}
                              onClick={() => void handleScope(request.request_id)}
                            >
                              Scope
                            </Button>
                          )}
                          <Button
                            size="sm"
                            disabled={!!pending[request.request_id]}
                            onClick={() => void handleApprove(request.request_id)}
                          >
                            Approve
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={!!pending[request.request_id]}
                            onClick={() => void handleReject(request.request_id)}
                          >
                            Reject
                          </Button>
                        </div>
                      )}
                    </li>
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
