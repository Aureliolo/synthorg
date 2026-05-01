/**
 * Workflow executions list.
 *
 * Lists recent runs for a single workflow definition with a Cancel
 * action for executions still in flight.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import type { AgentRuntimeStatus } from '@/lib/utils'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { ROUTES } from '@/router/routes'
import { formatDateTime } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'
import {
  cancelWorkflowExecution,
  listWorkflowExecutions,
  type WorkflowExecution,
} from '@/api/endpoints/workflow-executions'

const log = createLogger('WorkflowExecutionsPage')

const TERMINAL_STATUSES = new Set<WorkflowExecution['status']>([
  'completed',
  'failed',
  'cancelled',
])

// Map the workflow execution lifecycle onto the four-tone
// AgentRuntimeStatus that ``StatusBadge`` understands. ``running``
// maps to active (live, in flight), ``completed`` to active too
// (terminal-success), ``failed`` to error, ``cancelled`` to offline
// (terminal-but-not-an-error), ``pending`` to idle.
const STATUS_BADGE_MAP: Record<WorkflowExecution['status'], AgentRuntimeStatus> = {
  pending: 'idle',
  running: 'active',
  completed: 'active',
  failed: 'error',
  cancelled: 'offline',
}

export default function WorkflowExecutionsPage() {
  const { id } = useParams<{ id: string }>()
  const [executions, setExecutions] = useState<readonly WorkflowExecution[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingCancel, setPendingCancel] = useState<string | null>(null)
  const addToast = useToastStore((s) => s.add)
  // Per-request sequence number, bumped on every reload. The earlier
  // ``requestedFor !== id`` guard compared two values from the same
  // closure snapshot, so it never filtered out an older in-flight
  // request after the user switched workflows. A ref-backed counter
  // is the canonical pattern: the in-flight request remembers its
  // own id, the ref always reads the latest issued one, and a
  // mismatch unambiguously identifies a stale response.
  const requestSeqRef = useRef(0)
  // Latest workflow id, mirrored into a ref so the in-flight reload
  // can compare against the most recent route param at settle time.
  // The sequence guard alone leaves a window between the route
  // change and the next ``reload`` call where a still-pending
  // request could pass the sequence check and write the previous
  // workflow's rows into the new workflow's view; the id-ref check
  // closes that window by demanding the in-flight ``requestedFor``
  // still match the latest route param.
  const latestIdRef = useRef<string | undefined>(id)
  latestIdRef.current = id

  // When the operator navigates between workflows quickly (URL
  // changes drive ``id``), an in-flight response for the previous
  // workflow could otherwise land in the new workflow's table.
  // ``setExecutions([])`` clears the previous workflow's rows
  // immediately so the operator sees a clean transition instead of
  // stale data + spinner; the dual guard (sequence + id ref) drops
  // responses whose captured ``requestId`` no longer matches the
  // latest issued one OR whose captured ``requestedFor`` no longer
  // matches the latest route param.
  const reload = useCallback(async () => {
    if (!id) return
    const requestedFor = id
    requestSeqRef.current += 1
    const requestId = requestSeqRef.current
    setExecutions([])
    setLoading(true)
    setError(null)
    function isStale(): boolean {
      return (
        requestId !== requestSeqRef.current
        || latestIdRef.current !== requestedFor
      )
    }
    try {
      const rows = await listWorkflowExecutions(requestedFor)
      if (isStale()) return
      setExecutions(rows)
    } catch (err) {
      if (isStale()) return
      const message = getErrorMessage(err)
      // SEC-1: workflowId is URL-controlled, sanitize.
      log.error('listWorkflowExecutions failed', {
        workflowId: sanitizeForLog(requestedFor),
        error: sanitizeForLog(message),
      })
      setError(message)
    } finally {
      if (!isStale()) setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void reload()
  }, [reload])

  // Drop any in-flight cancel target when the workflow id changes.
  // The setState fires synchronously here on purpose: a deferred
  // microtask creates a race window where the user can click
  // Confirm on the dialog (still open with the previous workflow's
  // execution id) before the microtask settles, which then
  // dispatches a cancel against the wrong workflow.
  useEffect(() => {
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- correctness wins over the microtask defer here
    setPendingCancel(null)
  }, [id])

  // Capture the workflow id at the moment the cancel was issued so
  // ``handleCancel`` can detect navigation away from this view (the
  // dialog Confirm callback can resolve after a route change). The
  // success-path toast still fires, but ``reload()`` is gated on
  // the workflow id still matching ``latestIdRef.current``;
  // otherwise we'd be refetching the previous workflow's executions
  // while the operator is already looking at a different page.
  // The ``try`` only wraps the API call so a future addToast
  // refactor that throws (e.g. a queue overflow check) can't be
  // mistaken for an API failure and routed through the error toast.
  const handleCancel = useCallback(async (executionId: string) => {
    const issuedFor = id
    try {
      await cancelWorkflowExecution(executionId)
    } catch (err) {
      const message = getErrorMessage(err)
      log.error('cancelWorkflowExecution failed', {
        executionId: sanitizeForLog(executionId),
        error: sanitizeForLog(message),
      })
      addToast({
        variant: 'error',
        title: 'Could not cancel execution',
        description: message,
      })
      return
    }
    addToast({ variant: 'success', title: 'Cancellation requested' })
    if (latestIdRef.current === issuedFor) {
      void reload()
    }
  }, [addToast, reload, id])

  if (!id) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: 'Executions' }]} />
        <ErrorBanner severity="error" title="Missing workflow id in URL" />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs items={[{ label: 'Workflows', to: ROUTES.WORKFLOWS }, { label: id }, { label: 'Executions' }]} />
      <ListHeader title="Workflow executions" count={executions.length} />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load executions"
          description={error}
          onRetry={() => void reload()}
        />
      )}

      {loading && executions.length === 0 ? (
        <ProgressIndicator
          variant="indeterminate"
          label="Loading executions"
          description={`Fetching run history for ${id}`}
        />
      ) : !error && executions.length === 0 ? (
        <EmptyState
          title="No executions yet"
          description="Trigger this workflow to see its run history here."
        />
      ) : executions.length > 0 ? (
        <SectionCard title="Recent runs">
          <ul className="divide-y divide-border">
            {executions.map((row) => {
              const inFlight = !TERMINAL_STATUSES.has(row.status)
              return (
                <li key={row.id} className="flex items-center gap-4 py-2">
                  <span className="font-mono text-xs text-foreground">{row.id.slice(0, 8)}</span>
                  <StatusBadge
                    status={STATUS_BADGE_MAP[row.status]}
                    decorative
                  />
                  <span className="text-xs uppercase tracking-wide text-text-secondary">
                    {row.status}
                  </span>
                  <span className="flex-1 text-xs text-text-secondary">
                    {`Started ${formatDateTime(row.created_at)}`}
                  </span>
                  {row.error && (
                    <span className="truncate text-xs text-danger" title={row.error}>
                      {row.error}
                    </span>
                  )}
                  {inFlight && (
                    <Button
                      type="button"
                      size="xs"
                      variant="outline"
                      onClick={() => setPendingCancel(row.id)}
                    >
                      Cancel
                    </Button>
                  )}
                </li>
              )
            })}
          </ul>
        </SectionCard>
      ) : null}

      <ConfirmDialog
        open={pendingCancel !== null}
        title="Cancel execution?"
        description="The execution will stop at the next available checkpoint. This is best-effort and not always immediate."
        variant="destructive"
        confirmLabel="Cancel run"
        onOpenChange={(next) => { if (!next) setPendingCancel(null) }}
        onConfirm={async () => {
          // Capture the target id before awaiting so we can compare
          // against the latest state after the cancel resolves. Without
          // this, ``setPendingCancel(null)`` would close a *newly*
          // opened dialog if the user re-targeted a different execution
          // while the previous cancel was still in flight. The
          // functional update only clears when the slot still holds
          // our target.
          const target = pendingCancel
          if (!target) return
          await handleCancel(target)
          setPendingCancel((prev) => (prev === target ? null : prev))
        }}
      />
    </div>
  )
}
