import { useCallback, useState } from 'react'
import { Inbox } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'

import type { ClientRequest, RequestStatus } from '@/api/endpoints/clients'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ListHeader } from '@/components/ui/list-header'
import { RequestCard } from '@/components/ui/request-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SectionCard } from '@/components/ui/section-card'
import { SelectField } from '@/components/ui/select-field'
import { SkeletonCard } from '@/components/ui/skeleton'
import { useRequestQueue, type RequestQueueState } from './request-queue/useRequestQueue'

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

type RequestActionKind = 'scope' | 'approve' | 'reject'

interface RequestConfirmTarget {
  kind: RequestActionKind
  id: string
}

const ACTION_VERB: Record<RequestActionKind, string> = {
  scope: 'Scope',
  approve: 'Approve',
  reject: 'Reject',
}

const ACTION_PROMPT: Record<RequestActionKind, string> = {
  scope: 'Scoping advances the request through the state machine and is side-effecting.',
  approve: 'Approving advances the request towards task creation and cannot be undone.',
  reject: 'Rejecting cancels the request. This cannot be undone.',
}

interface RequestConfirmCopy {
  title: string
  description: string | undefined
  label: string
  variant: 'default' | 'destructive'
}

function buildConfirmCopy(target: RequestConfirmTarget | null): RequestConfirmCopy {
  if (!target) return { title: '', description: undefined, label: 'Confirm', variant: 'default' }
  return {
    title: `${ACTION_VERB[target.kind]} request ${target.id}?`,
    description: ACTION_PROMPT[target.kind],
    label: ACTION_VERB[target.kind],
    variant: target.kind === 'reject' ? 'destructive' : 'default',
  }
}

/**
 * Active request board with a confirmation step in front of every state
 * transition. Approving and scoping are irreversible state-machine walks, so a
 * named ConfirmDialog guards against a misclick. The wrapper callbacks are
 * stable (`useCallback`) so they do not invalidate the `RequestCard` memo.
 */
function RequestQueueActiveView({ q }: { q: RequestQueueState }) {
  const [confirm, setConfirm] = useState<RequestConfirmTarget | null>(null)

  const onScope = useCallback((id: string) => setConfirm({ kind: 'scope', id }), [])
  const onApprove = useCallback((id: string) => setConfirm({ kind: 'approve', id }), [])
  const onReject = useCallback((id: string) => setConfirm({ kind: 'reject', id }), [])

  const handleConfirm = useCallback(() => {
    if (!confirm) return
    if (confirm.kind === 'scope') q.handleScope(confirm.id)
    else if (confirm.kind === 'approve') q.handleApprove(confirm.id)
    else q.handleReject(confirm.id)
    setConfirm(null)
  }, [confirm, q])

  const copy = buildConfirmCopy(confirm)

  return (
    <>
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
      ) : q.filteredRequests.length === 0 ? (
        // Keep the filter bar above visible so the operator can adjust the
        // search/status that filtered every request out, rather than seeing
        // a wall of empty status columns.
        <EmptyState
          icon={Inbox}
          title="No requests match your filters"
          description="Adjust the search or status filter above to see the rest of the queue."
        />
      ) : (
        <RequestQueueBoard
          filteredRequests={q.filteredRequests}
          pending={q.pending}
          onScope={onScope}
          onApprove={onApprove}
          onReject={onReject}
        />
      )}

      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => { if (!open) setConfirm(null) }}
        title={copy.title}
        description={copy.description}
        confirmLabel={copy.label}
        variant={copy.variant}
        loading={confirm !== null && Boolean(q.pending[confirm.id])}
        onConfirm={handleConfirm}
      />
    </>
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

      <RequestQueueActiveView q={q} />
    </div>
  )
}
